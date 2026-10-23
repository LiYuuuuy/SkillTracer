"""Replay original call and state contracts with deterministic browser observations."""

import ast
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from test_workflow import FakeBrowser

from skilltracer.agents.skills import SkillReuser
from skilltracer.agents.summarizer import Assessment
from skilltracer.planning.plan import Plan
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.workflow import Workflow

REFERENCE = json.loads((Path(__file__).parent / "fixtures/workflow_reference.json").read_text())


def normalize_plan(value):
    if value is None:
        return None
    return copy.deepcopy(value.obj) if isinstance(value, Plan) else Plan.from_symbolic(value).obj


def normalize_call(method, arguments):
    values = {}
    for key in (
        "query",
        "task",
        "subgoal",
        "history_subgoals",
        "history_actions",
        "axtree",
        "current_axtree",
        "plan_status",
        "account_information",
        "executed_actions_list",
        "last_checkpoint",
        "filled_plan",
    ):
        if key in arguments:
            values[key] = copy.deepcopy(arguments[key])
    for key in ("past_plan", "executed_plan", "history_plan", "plan", "executed_nodes_list"):
        if key in arguments:
            values[key] = normalize_plan(arguments[key])
    if "plan_to_process" in arguments:
        values["plan_to_process"] = json.loads(arguments["plan_to_process"])
        # The new prefix hint is tested separately; the old projection stays comparable.
        values["plan_to_process"].pop("committed_node_ids", None)
    if "skill" in arguments:
        value = arguments["skill"]
        values["skill_id"] = json.loads(value)["id"] if value and value != "None" else None
    if "reused_skill" in arguments:
        value = arguments["reused_skill"]
        values["reused_skill_id"] = json.loads(value)["id"] if value and value != "None" else None
    if "executed_actions" in arguments:
        values["executed_actions"] = arguments["executed_actions"] or None
    if "delta_axtree_traj" in arguments:
        trajectory = arguments["delta_axtree_traj"].split("\n[PROGRAM_EDGE_CHECKS]\n", 1)[0]
        boundary = trajectory.find("\n[NODE_")
        # The initial tree now comes from the browser, rather than the old model slice.
        values["delta_axtree_traj"] = "[AXTREE_0]\n<browser observation>" + (
            trajectory[boundary:] if boundary >= 0 else ""
        )
    return {"method": method, "arguments": values}


def current_state(workflow):
    assets = workflow.assets
    task = assets.task
    state = {
        "steps": task.steps,
        "task_finished": task.finished,
        "completed_subgoals": list(task.subgoals),
        "task_nodes": [node["node_id"] for node in task.plan.get("nodes", [])],
        "task_actions": list(task.actions),
        "long_term_count": len(task.long_term),
        "candidate_ids": [item["id"] for item in task.candidate_skills],
    }
    if assets.subgoals:
        subgoal = assets.subgoals[-1]
        state.update(
            planning_rounds=subgoal.planning_rounds,
            last_plan_succeeded=subgoal.last_plan_succeeded,
            verified_nodes=[node["node_id"] for node in subgoal.plan.get("nodes", [])],
            verified_actions=list(subgoal.actions),
            past_nodes=[node["node_id"] for node in subgoal.past_plan.get("nodes", [])],
            guidance=list(subgoal.guidances),
            short_term=[entry.to_dict() for entry in subgoal.failures],
        )
        current = assets.current_plan
        if current is not None:
            state.update(
                plan_complete=current.complete,
                filled=current.filled,
                past_is_current=subgoal.past_plan is current.plan,
                last_failure_is_live_node=bool(subgoal.failures)
                and any(
                    subgoal.failures[-1].original_failed_node is node
                    for node in current.plan.get("nodes", [])
                ),
            )
    return copy.deepcopy(state)


class ReplayBrowser(FakeBrowser):
    def execute_browsergym_action(self, action):
        if action.startswith("goto("):
            self.url = ast.literal_eval(ast.parse(action, mode="eval").body.args[0])
            self.tree = "[10] link 'Target'"
        return super().execute_browsergym_action(action)


def run_current(case, directory, configure=None):
    root = Path(directory)
    repositories = Repositories.open(root / "state", "map")
    repositories.skills.write_back(REFERENCE["scenarios"][0]["expected"]["final"]["skills"])
    workflow = Workflow(
        {
            role: lambda _: "Unused model"
            for role in (
                "subgoal_generator",
                "planner",
                "executor",
                "summarizer",
                "skill_inducer",
                "skill_reuser",
            )
        },
        261,
        "map",
        "http://example.test",
        browser=ReplayBrowser(),
        evaluator_model="test",
        results_dir=root / "results",
        state_dir=root / "state",
    )
    events, states = [], []
    queues = {
        name: copy.deepcopy(case[name]) for name in ("subgoals", "plans", "bindings", "assessments")
    }

    def call(method, **kwargs):
        events.append(normalize_call(method, kwargs))
        states.append({"method": method, "state": current_state(workflow)})
        if method == "subgoal":
            goal, terminal = queues["subgoals"].pop(0)
            return goal, "[10] link 'Target'", terminal
        if method == "planner":
            return Plan(queues["plans"].pop(0)).to_symbolic() + "\nevidence: [10] link 'Target'"
        if method == "executor":
            return (
                "<plan components>:\n"
                + "\n".join(
                    f"'''{node_id}: {action}'''" for node_id, action in queues["bindings"].pop(0)
                )
                + "\n<context>:\n[10] link 'Target'"
            )
        if method == "summarizer":
            item = queues["assessments"].pop(0)
            if not item:
                return Assessment(True, kwargs["last_checkpoint"])
            return Assessment(
                False,
                kwargs["checkpoints"].checkpoints[
                    kwargs["checkpoints"].node_ids.index(item["error_node"])
                ],
                error_node=item["error_node"],
                error_evidence=item["error_evidence"],
                repair_signal=item["repair_signal"],
                insight=item["insight"],
                guidance=item["subgoal_guidance"],
            )
        if method == "inducer":
            value = copy.deepcopy(REFERENCE["scenarios"][0]["expected"]["final"]["skills"][0])
            value.update(id=case.get("skill_id", "new"), name="Produced fixture")
            return "'''skill\n" + json.dumps(value) + "\n'''"

    def evaluate(info):
        events.append(
            {
                "method": "evaluate",
                "arguments": {key: info[key] for key in ("intent", "response", "actions", "site")},
            }
        )
        states.append({"method": "evaluate", "state": current_state(workflow)})
        return {"status": case.get("evaluation", "success")}, "evaluation prompt"

    workflow.evaluator = evaluate
    if configure is not None:
        configure(workflow)
    with (
        patch.object(
            workflow.subgoal_generator, "generate", side_effect=lambda **kw: call("subgoal", **kw)
        ),
        patch.object(workflow.planner, "plan", side_effect=lambda **kw: call("planner", **kw)),
        patch.object(workflow.executor, "execute", side_effect=lambda **kw: call("executor", **kw)),
        patch.object(
            workflow.summarizer, "summarize", side_effect=lambda **kw: call("summarizer", **kw)
        ),
        patch.object(
            workflow.learning.inducer,
            "induce_skill",
            side_effect=lambda **kw: call("inducer", **kw),
        ),
        patch.object(
            workflow.learning.reuser, "reuse", return_value=(case.get("reuse", False), "s1")
        ),
    ):
        result = workflow.run("Open target", maximum_steps=case.get("max_steps", 10))
    task = workflow.assets.task
    return {
        "calls": events,
        "states": states,
        "browser_actions": workflow.browser.actions,
        "final": {
            "actions": result["executed_actions"],
            "plan": Plan.from_symbolic(result["executed_plan"]).obj,
            "subgoals": result["subgoals"],
            "skills": workflow.repositories.skills.read_all(),
            "long_term": [
                item.to_dict() for item in workflow.repositories.load_task("Next", 1).long_term
            ],
            "components": {
                purpose: sorted(values)
                for purpose, values in workflow.repositories.components.load().items()
            },
        },
        "loaded_skills_unchanged": task.skills
        == REFERENCE["scenarios"][0]["expected"]["final"]["skills"],
        "long_term_context_frozen": task.long_term_context == "",
        "browser_closed": workflow.browser.closed,
    }


def expected_with_binding_fix(entry):
    """Apply the intended strict-prefix contract where the old regex was broken."""
    expected = copy.deepcopy(entry["expected"])
    for call in expected["calls"]:
        if call["method"] == "executor":
            # Edges now stay in the runtime and are checked against the page.
            call["arguments"]["plan_to_process"].pop("edges", None)
        if "delta_axtree_traj" in call["arguments"]:
            trajectory = call["arguments"]["delta_axtree_traj"]
            boundary = trajectory.find("\n[NODE_")
            call["arguments"]["delta_axtree_traj"] = "[AXTREE_0]\n<browser observation>" + (
                trajectory[boundary:] if boundary >= 0 else ""
            )
    # Current memory records are immutable failure snapshots, rather than live plan aliases.
    original_nodes = {}
    for graph in entry["input"]["plans"]:
        for node in graph["nodes"]:
            original_nodes.setdefault(node["node_id"], copy.deepcopy(node))
    for item in expected["states"]:
        snapshot = item["state"]
        if "last_failure_is_live_node" in snapshot:
            snapshot["last_failure_is_live_node"] = False
        for memory in snapshot.get("short_term", []):
            memory["original_failed_node"] = copy.deepcopy(
                original_nodes[memory["original_failed_node"]["node_id"]]
            )
    prior_errors = [item for item in entry["input"]["assessments"] if item]
    for memory, previous in zip(expected["final"]["long_term"], prior_errors):
        memory["original_node"] = copy.deepcopy(original_nodes[memory["original_node"]["node_id"]])
        memory["error_evidence"] = copy.deepcopy(previous["error_evidence"])
        memory["success_repair"] = {
            "repair_tool": copy.deepcopy(previous["repair_signal"]),
            "insight": previous["insight"],
        }
    assessments = iter(entry["input"]["assessments"])
    failed_target = None
    for call, state in zip(expected["calls"], expected["states"], strict=True):
        if call["method"] == "executor" and failed_target is not None:
            filled = call["arguments"]["filled_plan"]
            prefix = []
            for line in filled.splitlines():
                if line.strip().startswith("'''" + failed_target + ":"):
                    break
                prefix.append(line)
            call["arguments"]["filled_plan"] = "\n".join(prefix)
            state["state"]["filled"] = "\n".join(prefix)
            failed_target = None
        if call["method"] == "summarizer":
            assessment = next(assessments)
            signal = assessment.get("repair_signal", {})
            if signal.get("tool") in {"REPLACE_ACTION", "REFINE_DESCRIPTION"}:
                failed_target = assessment["error_node"]
    return expected


class WorkflowReferenceTests(unittest.TestCase):
    def test_calls_and_state_transitions_match_twelve_original_workflows(self):
        for original in REFERENCE["scenarios"]:
            entry = copy.deepcopy(original)
            # Archived stubs sometimes emitted hover for a declared click. Align those
            # declarations before replay; primitive substitutions are now rejected.
            primitives = {}
            for binding in entry["input"]["bindings"]:
                for node_id, action in binding:
                    primitives.setdefault(node_id, action.split("(", 1)[0])

            def align(value):
                if isinstance(value, list):
                    for item in value:
                        align(item)
                elif isinstance(value, dict):
                    if value.get("node_id") in primitives and isinstance(value.get("action"), str):
                        value["action"] = (
                            primitives[value["node_id"]] + "(" + value["action"].split("(", 1)[1]
                        )
                    for key, child in value.items():
                        if key != "skills":
                            align(child)

            align(entry)
            case, expected = entry["input"], expected_with_binding_fix(entry)
            with self.subTest(scenario=case["name"]), tempfile.TemporaryDirectory() as directory:
                actual = run_current(case, directory)
                self.assertEqual(actual["calls"], expected["calls"])
                self.assertEqual(actual["browser_actions"], expected["browser_actions"])
                self.assertEqual(actual["final"], expected["final"])
                self.assertTrue(actual["loaded_skills_unchanged"])
                self.assertTrue(actual["long_term_context_frozen"])
                self.assertTrue(actual["browser_closed"])
                for old, new in zip(expected["states"], actual["states"], strict=True):
                    method = old["method"]
                    keys = {
                        "steps",
                        "completed_subgoals",
                        "task_nodes",
                        "task_actions",
                        "long_term_count",
                        "candidate_ids",
                    }
                    if method in {"executor", "summarizer", "planner"}:
                        keys |= {
                            "planning_rounds",
                            "last_plan_succeeded",
                            "verified_nodes",
                            "verified_actions",
                            "past_nodes",
                            "guidance",
                            "short_term",
                        }
                    if method in {"executor", "summarizer"}:
                        keys |= {
                            "filled",
                            "past_is_current",
                            "last_failure_is_live_node",
                            "plan_complete",
                        }
                    if method == "inducer":
                        # Verified task progress is retained before learning so errors cannot lose it.
                        keys -= {"task_nodes", "task_actions"}
                    if method == "evaluate":
                        keys.add("task_finished")
                    for key in keys & old["state"].keys():
                        self.assertEqual(
                            new["state"].get(key), old["state"][key], (case["name"], method, key)
                        )

    def test_skill_reuse_return_values_match_original_parser(self):
        for entry in REFERENCE["skill_reuse"]:
            with self.subTest(text=entry["text"]):
                self.assertEqual(
                    list(SkillReuser(lambda _: entry["text"]).reuse("Task", "Goal", [])),
                    entry["expected"],
                )

    def test_storage_calls_follow_original_order_and_stop_at_each_failure(self):
        order = (
            "components",
            "result",
            "skills",
            "long_term_memory",
            "subgoal_memory",
            "trajectory",
        )
        for failure in (None, *order):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
                ExitStack() as stack,
            ):
                calls, workflows = [], []

                def configure(workflow):
                    workflows.append(workflow)
                    from skilltracer.memory.experience import LongTermMemory

                    for name, owner, method in (
                        ("components", workflow.repositories.components, "write_back"),
                        ("result", workflow.artifacts, "save"),
                        ("skills", workflow.repositories.skills, "write_back"),
                        ("long_term_memory", LongTermMemory, "import_long_term_memories_to_txt"),
                        ("subgoal_memory", workflow.repositories.subgoals, "write_back"),
                        ("trajectory", workflow.artifacts, "save_trajectory"),
                    ):
                        original = getattr(owner, method)

                        def invoke(*args, name=name, original=original, **kwargs):
                            calls.append(name)
                            if failure == name:
                                raise OSError("Injected storage failure: " + name)
                            return original(*args, **kwargs)

                        stack.enter_context(patch.object(owner, method, side_effect=invoke))

                case = REFERENCE["scenarios"][0]["input"]
                if failure is None:
                    run_current(case, directory, configure)
                    self.assertEqual(calls, list(order))
                else:
                    with self.assertRaisesRegex(OSError, "Injected storage failure"):
                        run_current(case, directory, configure)
                    self.assertEqual(calls, list(order[: order.index(failure) + 1]))
                workflow = workflows[0]
                manifest = json.loads((workflow.assets.directory / "manifest.json").read_text())
                boundary = order.index(failure) if failure else len(order)
                for bank in ("components", "skills", "long_term_memory", "subgoal_memory"):
                    self.assertEqual(manifest["bank_commits"][bank], order.index(bank) < boundary)
                self.assertEqual((Path(directory) / "results/map/261.json").exists(), boundary > 1)
                self.assertEqual(
                    (Path(directory) / "results/map/trajectories/261.json").exists(),
                    failure is None,
                )
                self.assertTrue(workflow.browser.closed)

    def test_retrieval_and_candidate_lists_preserve_original_alias_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            repositories = Repositories.open(directory, "map")
            repositories.skills.write_back(REFERENCE["scenarios"][0]["expected"]["final"]["skills"])
            task = repositories.load_task("Open target", 10)
            self.assertIsNot(task.skills, task.candidate_skills)
            self.assertIs(task.skills[0], task.candidate_skills[0])
            from skilltracer.memory.skills import SkillRepository

            replacement = {**task.candidate_skills[0], "name": "Refined only in candidates"}
            SkillRepository.upsert_skill(task.candidate_skills, replacement)
            self.assertIs(task.candidate_skills[0], replacement)
            self.assertIsNot(task.skills[0], replacement)
            self.assertEqual(task.skills[0]["name"], "Fixture skill")
            self.assertEqual(repositories.skills.read_all()[0]["name"], "Fixture skill")

    def test_empty_plans_remain_bounded_by_eleven_subgoals_and_twenty_one_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = []

            def configure(workflow):
                captured.append(workflow)

            case = copy.deepcopy(REFERENCE["scenarios"][0]["input"])
            # Build a full bounded replay through the same call adapters.
            case["subgoals"] = [["Still working", False] for _ in range(11)]
            case["plans"] = [Plan.init_empty().to_dict() for _ in range(11 * 21)]
            case["bindings"], case["assessments"] = [], []
            case["evaluation"] = "failure"
            actual = run_current(case, directory, configure)
            workflow = captured[0]
            self.assertEqual(len(workflow.assets.subgoals), 11)
            self.assertTrue(all(goal.planning_rounds == 21 for goal in workflow.assets.subgoals))
            self.assertEqual(sum(call["method"] == "planner" for call in actual["calls"]), 231)
            self.assertEqual(workflow.assets.task.steps, 0)
            self.assertFalse(workflow.assets.task.finished)
            self.assertFalse(workflow.assets.bank_commits["components"])


if __name__ == "__main__":
    unittest.main()
