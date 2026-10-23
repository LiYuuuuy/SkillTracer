"""Exercise orchestration, rollback and persistence without network requests."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from skilltracer.llm import ROLES
from skilltracer.memory.skills import SkillRepository
from skilltracer.planning.plan import Plan
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.workflow import Workflow

SUCCESS_FLAGS = "true\ntrue\nfalse\ntrue\nfalse\ntrue"
EMPTY_FLAGS = "false\ntrue\ntrue\nfalse\nfalse\nfalse"


def plan_text(actions, terminal=True, component_type="link"):
    nodes = [
        {
            "node_id": f"n{index + 1}",
            "purpose": "navigation",
            "component_type": component_type,
            "action": action,
            "description": "Use the observed element.",
        }
        for index, action in enumerate(actions)
    ]
    return (
        Plan(
            {
                "nodes": nodes,
                "edges": [],
                "next_step_decision": {"should_plan_next": not terminal},
            }
        ).to_symbolic()
        + "\nevidence: [10] link 'Target'"
    )


def bound(*actions):
    return (
        "<plan components>:\n"
        + "\n".join(f"'''n{index + 1}: {action}'''" for index, action in enumerate(actions))
        + "\n<context>:\n[10] link 'Target'"
    )


def skill_text():
    return (
        "'''skill\n"
        + json.dumps(
            {
                "id": "new_skill",
                "name": "Read result",
                "description": "Read a resulting page",
                "plan": {
                    "nodes": [
                        {
                            "node_id": "n1",
                            "purpose": "navigation",
                            "component_type": "link",
                            "action": "click(target_id)",
                            "description": "Open the target",
                        }
                    ],
                    "edges": [],
                    "next_step_decision": {"should_plan_next": False},
                },
            }
        )
        + "\n'''"
    )


def repair_text(node, action):
    return json.dumps(
        {
            "error_node": node,
            "error_evidence": {"evidence": "Element unavailable"},
            "repair_signal": {
                "tool": "REPLACE_ACTION",
                "arguments": {"node_id": node, "new_action": action},
            },
            "insight": "Use the visible alternative.",
        }
    )


class QueueModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("Unexpected model call.")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeBrowser:
    def __init__(self):
        self.actions = []
        self.closed = False
        self.url = "http://example.test"
        self.tree = "[10] link 'Target'"
        self.error = ""

    def execute_browsergym_action(self, action):
        self.actions.append(action)
        self.error = "Element unavailable" if "'bad'" in action else ""
        if action.startswith("click") and not self.error:
            self.url = "http://example.test/result"
            self.tree = "[20] heading 'Result'\n\t[21] StaticText 'Grounded answer'"
        return not bool(self.error), False, False

    def get_page_state(self):
        return {"url": self.url, "axtree_object": self.tree}

    def get_browsergym_obs(self):
        return {"url": self.url, "last_action_error": self.error}

    def close(self):
        self.closed = True


class FakeEvaluator:
    def __init__(self, status="success"):
        self.status = status
        self.info = None

    def __call__(self, info):
        self.info = info
        return {"status": self.status, "thoughts": "Observed evidence checked."}, "eval prompt"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.browser = FakeBrowser()
        self.evaluator = FakeEvaluator()

    def tearDown(self):
        self.temporary.cleanup()

    def make_workflow(
        self, *, executor, summaries, actions=None, terminal=True, skill=None, initial_skills=True
    ):
        repositories = Repositories.open(self.root / "state", "map")
        if initial_skills and not repositories.skills.read_all():
            example = SkillRepository.parse_skill_json_from_triple_skill(skill_text())
            repositories.skills.write_back(
                [
                    {**example, "id": f"s{index}", "name": f"Test fixture {index}"}
                    for index in (1, 2, 3)
                ]
            )
        models = {
            "subgoal_generator": QueueModel(
                [
                    f"subgoal: Open the target\nterminate: {str(terminal).lower()}\nevidence: [10] link 'Target'",
                ]
            ),
            "planner": QueueModel([plan_text(actions or ["click(target_id)"])]),
            "executor": QueueModel(executor),
            "summarizer": QueueModel(summaries),
            "skill_reuser": QueueModel(["match: false\nid: null"]),
            "skill_inducer": QueueModel([skill] if skill else []),
        }
        self.assertEqual(set(models), set(ROLES))
        self.models = models
        return Workflow(
            models,
            261,
            "map",
            "http://example.test",
            results_dir=self.root / "results",
            state_dir=self.root / "state",
            browser=self.browser,
            evaluator=self.evaluator,
        )

    def test_success_saves_requested_directory_and_closes_browser(self):
        workflow = self.make_workflow(executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS])
        outcome = workflow.run("Open the target", maximum_steps=1)
        self.assertEqual(outcome["evaluation"]["status"], "success")
        self.assertEqual(outcome["executed_actions"], ["click('10')"])
        self.assertTrue(outcome["finished"])
        self.assertTrue(self.browser.closed)

    def test_invalid_planner_json_is_rejected_before_execution_and_retried(self):
        workflow = self.make_workflow(executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS])
        self.models["planner"].responses.insert(0, '```plan\n{"nodes":broken}\n```')
        outcome = workflow.run("Open target", maximum_steps=3)
        self.assertEqual(outcome["evaluation"]["status"], "success")
        self.assertEqual(outcome["execution_steps"], 1)
        self.assertEqual(len(self.models["planner"].prompts), 2)
        events = (workflow.assets.directory / "events.jsonl").read_text()
        self.assertIn('"event": "plan_rejected"', events)

    def test_skipped_executor_frontier_replans_without_executing_the_skipped_action(self):
        workflow = self.make_workflow(
            executor=[
                "<plan components>:\n'''n2: click('bad')'''\n<context>:",
                bound("click('10')"),
            ],
            summaries=[SUCCESS_FLAGS],
        )
        self.models["planner"].responses = [
            plan_text(["click(first_id)", "click(second_id)"]),
            plan_text(["click(target_id)"]),
        ]
        outcome = workflow.run("Open target", maximum_steps=3)
        self.assertEqual(outcome["evaluation"]["status"], "success")
        self.assertNotIn("click('bad')", self.browser.actions)
        self.assertEqual(outcome["executed_actions"], ["click('10')"])
        self.assertIn(
            '"event": "execution_rejected"',
            (workflow.assets.directory / "events.jsonl").read_text(),
        )
        self.assertTrue((self.root / "results/map/261.json").is_file())
        self.assertTrue((self.root / "results/map/trajectories/261.json").is_file())
        self.assertFalse(self.models["skill_inducer"].prompts)
        assets = Path(outcome["assets_dir"])
        manifest = json.loads((assets / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "success")
        self.assertTrue(manifest["learning_committed"])
        self.assertTrue(all((assets / name).is_file() for name in manifest["files"]))
        self.assertEqual(
            set(json.loads((self.root / "results/map/261.json").read_text())),
            {"task_id", "domain", "raw", "assets_dir"},
        )

    def test_planner_executor_and_inducer_receive_the_original_state_scopes(self):
        workflow = self.make_workflow(
            executor=[bound("click('10')"), bound("hover('20')").replace("n1:", "n2:")],
            summaries=[SUCCESS_FLAGS, SUCCESS_FLAGS],
            skill=skill_text(),
        )
        self.models["skill_reuser"].responses = ["match: true\nid: s1"]
        existing = workflow.repositories.skills.read_all()
        for skill in existing:
            skill["description"] = "Open the target submenu"
        workflow.repositories.skills.write_back(existing)
        self.models["planner"].responses = [
            plan_text(["click(target_id)"], terminal=False),
            plan_text(["hover(target_id)"]).replace('"n1"', '"n2"'),
        ]
        captured = []
        plan = workflow.planner.plan

        def capture_plan(**kwargs):
            captured.append(deepcopy(kwargs))
            return plan(**kwargs)

        with (
            patch.object(workflow.planner, "plan", side_effect=capture_plan),
            patch.object(workflow.executor, "execute", wraps=workflow.executor.execute) as executor,
            patch.object(
                workflow.learning.inducer,
                "induce_skill",
                wraps=workflow.learning.inducer.induce_skill,
            ) as inducer,
        ):
            workflow.run("Expose the target submenu", maximum_steps=3)
        first, second = captured
        self.assertIn('"id": "s1"', first["skill"])
        self.assertEqual(second["skill"], first["skill"])
        self.assertIn("[20] heading", second["axtree"])
        self.assertEqual(second["history_actions"], [])
        self.assertIn("n1", second["executed_plan"])
        self.assertIn("n1", second["past_plan"])
        self.assertTrue(second["plan_status"])
        self.assertTrue(all(call.kwargs["filled_plan"] is None for call in executor.call_args_list))
        self.assertIn('"id": "s1"', inducer.call_args.kwargs["reused_skill"])
        self.assertIn("n1", inducer.call_args.kwargs["executed_plan"])
        self.assertIn("n2", inducer.call_args.kwargs["executed_plan"])

    def test_partial_bank_write_failure_retains_original_order_and_accurate_commit_flags(self):
        workflow = self.make_workflow(
            executor=[bound("click('10')", "memorize('20')")],
            summaries=[SUCCESS_FLAGS],
            actions=["click(target_id)", "memorize(result_id)"],
            skill=skill_text(),
        )
        with patch(
            "skilltracer.runtime.repositories.LongTermMemory.import_long_term_memories_to_txt",
            side_effect=OSError("Memory store unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "Memory store unavailable"):
                workflow.run("Retain the result", maximum_steps=2)
        manifest = json.loads((workflow.assets.directory / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "error")
        self.assertFalse(manifest["learning_committed"])
        self.assertTrue(manifest["bank_commits"]["skills"])
        self.assertTrue(manifest["bank_commits"]["components"])
        self.assertFalse(manifest["bank_commits"]["long_term_memory"])
        self.assertFalse(manifest["bank_commits"]["subgoal_memory"])
        self.assertNotEqual(
            (self.root / "state/map/skills.txt").read_bytes(),
            b"",
        )
        self.assertTrue((self.root / "results/map/261.json").is_file())
        self.assertTrue(self.browser.closed)

    def test_final_budget_step_remains_in_evaluation_and_trajectory(self):
        self.evaluator.status = "failure"
        workflow = self.make_workflow(
            executor=[bound("click('10')")],
            summaries=[SUCCESS_FLAGS],
            terminal=False,
        )
        outcome = workflow.run("Open the target", maximum_steps=1)
        self.assertFalse(outcome["finished"])
        self.assertEqual(outcome["execution_steps"], 1)
        self.assertEqual(self.evaluator.info["actions"], ["click('10')"])
        self.assertEqual(outcome["executed_actions"], ["click('10')"])

    def test_browser_is_closed_after_model_exception(self):
        workflow = self.make_workflow(
            executor=[RuntimeError("Provider unavailable")],
            summaries=[],
        )
        with self.assertRaisesRegex(RuntimeError, "Provider unavailable"):
            workflow.run("Open the target", maximum_steps=1)
        self.assertTrue(self.browser.closed)
        assets = workflow.assets.directory
        manifest = json.loads((assets / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "error")
        self.assertFalse(manifest["learning_committed"])
        calls = [
            json.loads(line) for line in (assets / "model_calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(calls[-1]["role"], "executor")
        self.assertEqual(calls[-1]["error"]["message"], "Provider unavailable")
        self.assertTrue(json.loads((assets / "progress.json").read_text())["current_plan"])
        self.assertEqual(json.loads((assets / "progress.json").read_text())["execution_steps"], 1)

    def test_judge_provider_value_error_propagates_without_replanning(self):
        workflow = self.make_workflow(executor=[bound("click('10')")], summaries=[])
        self.models["summarizer"].responses = [ValueError("Provider rejected the request")]
        with self.assertRaisesRegex(ValueError, "Provider rejected"):
            workflow.run("Open target", maximum_steps=3)
        self.assertEqual(len(self.models["planner"].prompts), 1)
        self.assertIsNone(self.evaluator.info)
        self.assertTrue(self.browser.closed)
        manifest = json.loads((workflow.assets.directory / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "error")
        self.assertNotIn(
            "assessment_rejected", (workflow.assets.directory / "events.jsonl").read_text()
        )

    def test_rollback_value_error_propagates_after_recording_repair_state(self):
        workflow = self.make_workflow(
            executor=[bound("click('bad')")],
            summaries=[SUCCESS_FLAGS, repair_text("n1", "click(alternate_id)")],
        )
        original = self.browser.execute_browsergym_action

        def execute(action):
            if action.startswith("goto(") and self.browser.actions:
                raise ValueError("Browser replay failed")
            return original(action)

        self.browser.execute_browsergym_action = execute
        with self.assertRaisesRegex(ValueError, "Browser replay failed"):
            workflow.run("Open target", maximum_steps=3)
        self.assertEqual(len(self.models["planner"].prompts), 1)
        self.assertEqual(len(workflow.assets.subgoals[0].failures), 1)
        self.assertIsNone(self.evaluator.info)
        self.assertTrue(self.browser.closed)
        self.assertNotIn(
            "assessment_rejected", (workflow.assets.directory / "events.jsonl").read_text()
        )

    def test_asset_value_error_is_not_treated_as_a_model_assessment(self):
        workflow = self.make_workflow(executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS])
        record = workflow.artifacts.record

        def write(event, **details):
            if event == "progress_committed":
                raise ValueError("Asset serialization failed")
            return record(event, **details)

        with patch.object(workflow.artifacts, "record", side_effect=write):
            with self.assertRaisesRegex(ValueError, "Asset serialization failed"):
                workflow.run("Open target", maximum_steps=3)
        self.assertEqual(len(self.models["planner"].prompts), 1)
        self.assertEqual(workflow.assets.subgoals[0].actions, ["click('10')"])
        self.assertIsNone(self.evaluator.info)
        self.assertTrue(self.browser.closed)

    def test_unknown_repair_target_falls_back_to_current_first_node(self):
        repair = json.dumps(
            {
                "error_node": "n999",
                "repair_signal": {
                    "tool": "REPLACE_ACTION",
                    "arguments": {"node_id": "n999", "new_action": "click(other_id)"},
                },
            },
        )
        workflow = self.make_workflow(
            executor=[bound()],
            summaries=[EMPTY_FLAGS, repair],
        )
        self.evaluator.status = "failure"
        outcome = workflow.run("Open the target", maximum_steps=1)
        self.assertEqual(outcome["executed_actions"], [])
        self.assertEqual(self.browser.actions, ["goto('http://example.test')"] * 2)
        self.assertTrue(self.browser.closed)
        calls = (workflow.assets.directory / "model_calls.jsonl").read_text()
        self.assertIn("n999", calls)
        events = [
            json.loads(line)
            for line in (workflow.assets.directory / "events.jsonl").read_text().splitlines()
        ]
        resolved = next(
            event["data"] for event in events if event["event"] == "repair_node_resolved"
        )
        self.assertEqual(resolved["resolved_node"], "n1")
        self.assertEqual(resolved["reason"], "first_uncommitted_node")

    def test_real_action_failure_forces_repair_even_if_judge_claims_success(self):
        repair = json.dumps(
            {
                "error_node": "n1",
                "error_evidence": {"evidence": "Element unavailable"},
                "repair_signal": {
                    "tool": "REPLACE_ACTION",
                    "arguments": {
                        "node_id": "n1",
                        "new_action": "click(alternate_id)",
                    },
                },
                "insight": "Use the visible alternative.",
                "subgoal_guidance": "Identify the visible target before acting.",
            }
        )
        workflow = self.make_workflow(
            executor=[bound("click('bad')"), bound("click('good')")],
            summaries=[SUCCESS_FLAGS, repair, SUCCESS_FLAGS],
        )
        outcome = workflow.run("Open the target", maximum_steps=2)
        self.assertEqual(outcome["executed_actions"], ["click('good')"])
        self.assertEqual(self.browser.actions.count("goto('http://example.test')"), 2)
        self.assertEqual(outcome["evaluation"]["status"], "success")
        assets = Path(outcome["assets_dir"])
        memories = json.loads((assets / "short_term_memory.json").read_text())
        self.assertEqual(memories[0]["entries"][0]["original_failed_node"]["node_id"], "n1")
        self.assertFalse(memories[0]["entries"][0]["usable"])
        events = [json.loads(line) for line in (assets / "events.jsonl").read_text().splitlines()]
        attributions = [
            event["data"]["assessment"] for event in events if event["event"] == "assessment"
        ]
        self.assertEqual(attributions[0]["repair_signal"]["tool"], "REPLACE_ACTION")
        experience = json.loads((assets / "subgoal_memory.json").read_text())
        self.assertEqual(set(experience), {"produced"})
        stored = next(item for item in experience["produced"] if item["task"] == "Open the target")
        self.assertEqual(stored["guidance"], "Identify the visible target before acting.")

    def test_memorize_sees_page_after_navigation_and_only_produced_skill_is_saved(self):
        workflow = self.make_workflow(
            executor=[bound("click('10')", "memorize('20')")],
            summaries=[SUCCESS_FLAGS],
            actions=["click(target_id)", "memorize(result_id)"],
            skill=skill_text(),
        )
        original = (self.root / "state/map/skills.txt").read_bytes()
        outcome = workflow.run("Open and retain the result", maximum_steps=2)
        self.assertIn("Grounded answer", self.evaluator.info["captions"])
        self.assertNotIn("memorize('20')", self.browser.actions)
        self.assertIn("memorize('20')", outcome["executed_actions"])
        self.assertNotEqual((self.root / "state/map/skills.txt").read_bytes(), original)
        assets = Path(outcome["assets_dir"])
        memory = json.loads((assets / "active_memory.json").read_text())
        self.assertIn("Grounded answer", memory["20"])
        skills = json.loads((assets / "skills.json").read_text())
        self.assertEqual(len(skills["produced"]), 1)
        self.assertNotIn("initial", skills)
        self.assertNotIn("candidates", skills)

    def test_failed_task_does_not_persist_staged_learning(self):
        self.evaluator.status = "failure"
        workflow = self.make_workflow(executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS])
        before = {
            name: (self.root / "state/map" / name).read_bytes()
            for name in ("skills.txt", "components.txt", "subgoals.txt", "memory.txt")
        }
        workflow.run("Open the target", maximum_steps=1)
        for filename in ["skills.txt", "components.txt", "subgoals.txt", "memory.txt"]:
            self.assertEqual(
                (self.root / "state/map" / filename).read_bytes(),
                before[filename],
            )

    def test_failed_task_keeps_skills_staged_but_commits_component_discoveries(self):
        self.evaluator.status = "failure"
        actions = ["click(target_id)", "memorize(result_id)"]
        workflow = self.make_workflow(
            executor=[bound("click('10')", "memorize('20')")],
            summaries=[SUCCESS_FLAGS],
            actions=actions,
            skill=skill_text(),
        )
        self.models["planner"].responses = [plan_text(actions, component_type="evidence_panel")]
        before = {
            name: (self.root / "state/map" / name).read_bytes()
            for name in ("skills.txt", "subgoals.txt", "memory.txt")
        }
        outcome = workflow.run("Open and retain the result", maximum_steps=2)
        assets = Path(outcome["assets_dir"])
        self.assertEqual(len(json.loads((assets / "skills.json").read_text())["produced"]), 1)
        components = json.loads((assets / "components.json").read_text())
        self.assertIn(
            {"purpose": "navigation", "component_type": "evidence_panel"}, components["produced"]
        )
        self.assertFalse(json.loads((assets / "manifest.json").read_text())["learning_committed"])
        self.assertIn("evidence_panel", (self.root / "state/map/components.txt").read_text())
        self.assertTrue(
            json.loads((assets / "manifest.json").read_text())["bank_commits"]["components"]
        )
        for filename in ["skills.txt", "subgoals.txt", "memory.txt"]:
            self.assertEqual(
                (self.root / "state/map" / filename).read_bytes(),
                before[filename],
            )

    def test_two_single_node_subgoals_do_not_induce_a_combined_skill(self):
        workflow = self.make_workflow(
            executor=[bound("click('10')"), bound("click('20')").replace("n1:", "n2:")],
            summaries=[SUCCESS_FLAGS, SUCCESS_FLAGS],
        )
        self.models["subgoal_generator"].responses = [
            "subgoal: Open first target\nterminate: false\nevidence: [10] link 'Target'",
            "subgoal: Open second target\nterminate: true\nevidence: [20] heading 'Result'",
        ]
        self.models["skill_reuser"].responses *= 2
        self.models["planner"].responses = [
            plan_text(["click(first_id)"]),
            plan_text(["click(second_id)"]).replace('"n1"', '"n2"'),
        ]
        outcome = workflow.run("Open both targets", maximum_steps=3)
        self.assertEqual(len(Plan.from_symbolic(outcome["executed_plan"]).get("nodes")), 2)
        self.assertFalse(self.models["skill_inducer"].prompts)
        skills = json.loads((Path(outcome["assets_dir"]) / "skills.json").read_text())
        self.assertEqual(len(skills["induction_decisions"]), 2)
        for decision in skills["induction_decisions"]:
            self.assertEqual(decision["verified_nodes"], 1)
            self.assertEqual(decision["skill_skip_reasons"], ["verified_plan_too_short"])

    def test_two_plans_for_one_subgoal_induce_one_skill(self):
        workflow = self.make_workflow(
            executor=[bound("click('10')"), bound("hover('20')").replace("n1:", "n2:")],
            summaries=[SUCCESS_FLAGS, SUCCESS_FLAGS],
            skill=skill_text(),
        )
        self.models["planner"].responses = [
            plan_text(["click(target_id)"], terminal=False),
            plan_text(["hover(target_id)"]).replace('"n1"', '"n2"'),
        ]
        outcome = workflow.run("Expose the target submenu", maximum_steps=3)
        self.assertEqual(len(self.models["skill_inducer"].prompts), 1)
        skills = json.loads((Path(outcome["assets_dir"]) / "skills.json").read_text())
        self.assertEqual(len(skills["produced"]), 1)
        self.assertTrue(skills["induction_decisions"][0]["induce_skill"])

    def test_empty_final_plan_preserves_completion_metadata_for_skill_induction(self):
        actions = ["click(target_id)", "memorize(result_id)"]
        workflow = self.make_workflow(
            executor=[bound("click('10')", "memorize('20')")],
            summaries=[SUCCESS_FLAGS],
            actions=actions,
            skill=skill_text(),
        )
        self.models["planner"].responses = [plan_text(actions, terminal=False), plan_text([])]
        outcome = workflow.run("Read and retain the result", maximum_steps=2)
        self.assertFalse(
            Plan.from_symbolic(outcome["executed_plan"]).get("next_step_decision")[
                "should_plan_next"
            ]
        )
        self.assertIn('"should_plan_next": false', self.models["skill_inducer"].prompts[0])

    def test_replanning_uses_only_final_round_guidance_for_subgoal_memory(self):
        repair = json.dumps(
            {
                "error_node": "n1",
                "repair_signal": {
                    "tool": "REPLACE_PURPOSE",
                    "arguments": {"node_id": "n1", "new_purpose": "filter"},
                },
                "insight": "Find the correct target first.",
                "subgoal_guidance": "Clarify the target before acting.",
            }
        )
        workflow = self.make_workflow(
            executor=[bound("click('bad')"), bound("click('good')")],
            summaries=[SUCCESS_FLAGS, repair, SUCCESS_FLAGS],
        )
        self.models["planner"].responses *= 2
        outcome = workflow.run("Resolve the target with a new plan", maximum_steps=3)
        self.assertEqual(
            (self.root / "state/map/subgoals.txt").read_bytes(),
            b"",
        )
        events = (Path(outcome["assets_dir"]) / "events.jsonl").read_text()
        self.assertIn("Clarify the target before acting.", events)

    def test_component_discoveries_survive_a_later_model_exception(self):
        workflow = self.make_workflow(
            executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS], terminal=False
        )
        self.models["planner"].responses = [
            plan_text(["click(target_id)"], component_type="observed_panel")
        ]
        self.models["subgoal_generator"].responses.append(RuntimeError("Next subgoal unavailable"))
        with self.assertRaisesRegex(RuntimeError, "Next subgoal unavailable"):
            workflow.run("Continue after the first target", maximum_steps=3)
        self.assertIn("observed_panel", (self.root / "state/map/components.txt").read_text())
        manifest = json.loads((workflow.assets.directory / "manifest.json").read_text())
        self.assertTrue(manifest["bank_commits"]["components"])
        self.assertFalse(manifest["bank_commits"]["skills"])

    def test_type_repair_is_learned_only_after_a_verified_replan(self):
        repair = json.dumps(
            {
                "error_node": "n1",
                "repair_signal": {
                    "tool": "REPLACE_COMPONENT_TYPE",
                    "arguments": {"node_id": "n1", "new_component_type": "observed_panel"},
                },
                "insight": "Target the observed panel.",
            }
        )
        self.evaluator.status = "failure"
        workflow = self.make_workflow(
            executor=[bound("click('bad')"), bound("click('good')")],
            summaries=[SUCCESS_FLAGS, repair, SUCCESS_FLAGS],
        )
        self.models["planner"].responses.append(
            plan_text(["click(target_id)"], component_type="observed_panel")
        )
        workflow.run("Find the observed panel", maximum_steps=3)
        self.assertIn("observed_panel", (self.root / "state/map/components.txt").read_text())

    def test_unverified_type_repair_does_not_change_the_component_bank(self):
        repair = json.dumps(
            {
                "error_node": "n1",
                "repair_signal": {
                    "tool": "REPLACE_COMPONENT_TYPE",
                    "arguments": {"node_id": "n1", "new_component_type": "unverified_panel"},
                },
                "insight": "Try another observed type.",
            }
        )
        self.evaluator.status = "failure"
        workflow = self.make_workflow(
            executor=[bound("click('bad')")], summaries=[SUCCESS_FLAGS, repair]
        )
        outcome = workflow.run("Find the right target", maximum_steps=1)
        self.assertNotIn("unverified_panel", (self.root / "state/map/components.txt").read_text())
        skills = json.loads((Path(outcome["assets_dir"]) / "skills.json").read_text())
        self.assertEqual(
            skills["induction_decisions"][0]["component_skip_reasons"], ["step_budget_exhausted"]
        )

    def test_skill_with_an_existing_id_is_refined_without_growing_the_bank(self):
        skill = skill_text().replace('"new_skill"', '"s1"')
        workflow = self.make_workflow(
            executor=[bound("click('10')", "memorize('20')")],
            summaries=[SUCCESS_FLAGS],
            actions=["click(target_id)", "memorize(result_id)"],
            skill=skill,
        )
        self.models["skill_reuser"].responses = ["match: true\nid: s1"]
        outcome = workflow.run("Read and retain the result", maximum_steps=2)
        skills = json.loads((Path(outcome["assets_dir"]) / "skills.json").read_text())
        self.assertEqual(len(workflow.repositories.skills.read_all()), 3)
        self.assertEqual([item["id"] for item in skills["produced"]], ["s1"])
        self.assertEqual(skills["produced"][0]["name"], "Read result")

    def test_long_term_promotion_is_preserved_with_its_short_term_history(self):
        workflow = self.make_workflow(
            actions=["click(first_id)", "click(second_id)"],
            executor=[
                bound("click('bad')"),
                bound("click('good')", "click('bad')"),
                "<plan components>:\n'''n2: click('good')'''\n<context>:\n[10] link 'Target'",
            ],
            summaries=[
                SUCCESS_FLAGS,
                repair_text("n1", "click(alternate_first_id)"),
                SUCCESS_FLAGS,
                repair_text("n2", "click(alternate_second_id)"),
                SUCCESS_FLAGS,
            ],
        )
        outcome = workflow.run("Open both targets", maximum_steps=3)
        assets = Path(outcome["assets_dir"])
        long_term = json.loads((assets / "long_term_memory.json").read_text())
        self.assertEqual(len(long_term["produced"]), 1)
        short_term = json.loads((assets / "short_term_memory.json").read_text())
        self.assertEqual(len(short_term[0]["entries"]), 2)
        self.assertFalse(short_term[0]["entries"][0]["editable"])
        bank = [
            json.loads(line)
            for line in (self.root / "state/map/memory.txt").read_text().splitlines()
        ]
        self.assertEqual(bank, long_term["produced"])

    def test_browser_exception_mid_batch_keeps_completed_and_pending_actions(self):
        original = self.browser.execute_browsergym_action

        def execute(action):
            if action == "click('explode')":
                raise RuntimeError("Browser disconnected")
            return original(action)

        self.browser.execute_browsergym_action = execute
        workflow = self.make_workflow(
            actions=["click(first_id)", "click(second_id)"],
            executor=[bound("click('10')", "click('explode')")],
            summaries=[],
        )
        with self.assertRaisesRegex(RuntimeError, "Browser disconnected"):
            workflow.run("Open both targets", maximum_steps=1)
        assets = workflow.assets.directory
        events = [json.loads(line) for line in (assets / "events.jsonl").read_text().splitlines()]
        completed = [
            event["data"]["action"] for event in events if event["event"] == "action_finished"
        ]
        started = [
            event["data"]["action"] for event in events if event["event"] == "action_started"
        ]
        self.assertEqual(completed, ["click('10')"])
        self.assertEqual(started, ["click('10')", "click('explode')"])
        self.assertEqual(json.loads((assets / "manifest.json").read_text())["status"], "error")
        self.assertTrue(self.browser.closed)
        progress = json.loads((assets / "progress.json").read_text())
        self.assertEqual(progress["execution_steps"], 1)
        self.assertIn("explode", progress["current_plan"]["filled"])

    def test_rerunning_in_same_results_directory_keeps_both_asset_attempts(self):
        first = self.make_workflow(executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS])
        first_assets = Path(first.run("Open the target", maximum_steps=1)["assets_dir"])
        original = (first_assets / "events.jsonl").read_bytes()
        second = self.make_workflow(executor=[bound("click('10')")], summaries=[SUCCESS_FLAGS])
        second_assets = Path(second.run("Open the target", maximum_steps=1)["assets_dir"])
        self.assertNotEqual(first_assets, second_assets)
        self.assertEqual((first_assets / "events.jsonl").read_bytes(), original)
        self.assertEqual(len(list((self.root / "results/map/assets/261").iterdir())), 2)

    def test_keyboard_interrupt_saves_last_progress_and_closes_browser(self):
        workflow = self.make_workflow(executor=[KeyboardInterrupt()], summaries=[])
        with self.assertRaises(KeyboardInterrupt):
            workflow.run("Open the target", maximum_steps=1)
        assets = workflow.assets.directory
        manifest = json.loads((assets / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "interrupted")
        self.assertEqual(manifest["error"]["type"], "KeyboardInterrupt")
        self.assertTrue(json.loads((assets / "progress.json").read_text())["current_plan"])
        self.assertTrue(self.browser.closed)


if __name__ == "__main__":
    unittest.main()
