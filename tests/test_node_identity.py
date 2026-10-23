"""Regress cold-start retrieval, legacy node fallback and aligned execution commits."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_workflow import FakeBrowser, QueueModel

from skilltracer.agents.executor import Executor
from skilltracer.agents.summarizer import InvalidAssessment, Summarizer
from skilltracer.memory.active import ActiveMemory
from skilltracer.planning.checkpoints import CheckPointManager, RollBackPointer
from skilltracer.planning.plan import Plan
from skilltracer.planning.reuse import manage_skill_plan
from skilltracer.runtime.execution import ExecutionService
from skilltracer.runtime.learning import LearningService
from skilltracer.runtime.repair import RepairService
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.results import ArtifactStore
from skilltracer.runtime.state import PlanState, SubgoalState

PASS = "true\ntrue\nfalse\ntrue\nfalse\ntrue"
FAIL = "false\nfalse\nfalse\nfalse\nfalse\nfalse"


def node(node_id, action="click(target_id)"):
    return {
        "node_id": node_id,
        "purpose": "navigation",
        "component_type": "link",
        "action": action,
        "description": "Open the observed target.",
    }


def plan(nodes, edges=None, *, complete=True):
    return Plan(
        {
            "nodes": nodes,
            "edges": edges or [],
            "next_step_decision": {"should_plan_next": not complete},
        }
    )


def bound(*pairs):
    return (
        "<plan components>:\n"
        + "\n".join(f"'''{node_id}: {action}'''" for node_id, action in pairs)
        + "\n<context>: [10] link 'Target'"
    )


def assess(model, current, committed=None):
    agent = Summarizer(model)
    result = agent.summarize(
        query="Open target",
        subgoal="Open target",
        plan=current.to_symbolic(),
        delta_axtree_traj="Observed",
        executed_actions="",
        executed_actions_list=[],
        executed_nodes_list=(committed or Plan.init_empty()).to_symbolic(),
        active_memory="",
        short_term_memory="",
        long_term_memory="",
        checkpoints=CheckPointManager(current.to_dict()),
        last_checkpoint="c0",
    )
    return agent, result


def repair(error, argument):
    return json.dumps(
        {
            "error_node": error,
            "repair_signal": {
                "tool": "REPLACE_ACTION",
                "arguments": {"node_id": argument, "new_action": "click(observed_id)"},
            },
        }
    )


class RetrievalThresholdTests(unittest.TestCase):
    def test_one_or_two_skills_reach_reuser_and_top_k_is_only_an_upper_bound(self):
        for count in (1, 2, 4):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                repositories = Repositories.open(directory, "map")
                skills = [
                    {
                        "id": f"s{i}",
                        "name": "Open target",
                        "description": "Open target",
                        "plan": plan([node("n1")]).to_dict(),
                    }
                    for i in range(1, count + 1)
                ]
                repositories.skills.write_back(skills)
                task = repositories.load_task("Open target", 5)
                reuser = Mock()
                reuser.reuse.return_value = (True, "s1")
                result = LearningService(repositories, reuser, Mock()).select_skill(
                    task, "Open target"
                )
                self.assertEqual(json.loads(result)["id"], "s1")
                self.assertEqual(len(reuser.reuse.call_args.args[2]), min(count, 3))
                self.assertEqual(task.skills, skills)

    def test_empty_bank_skips_model_call(self):
        reuser = Mock()
        self.assertEqual(
            LearningService(Mock(), reuser, Mock()).select_skill(SimpleNamespace(skills=[]), "g"),
            "None",
        )
        reuser.reuse.assert_not_called()


class PlannerIdentityTests(unittest.TestCase):
    def test_restarted_ids_are_renumbered_with_conditions_and_literals_preserved(self):
        previous = plan([node("n20")])
        graph = plan(
            [node("n1", "hover('n1')"), node("n2", "memorize(result_id)")],
            [{"from": "n1", "to": "n2", "precondition": "exists_content('n1')"}],
        ).to_dict()
        original = copy.deepcopy(graph)
        decision = manage_skill_plan(json.dumps(graph), previous, "None")
        self.assertEqual(decision.planner_node_mapping, {"n1": "n21", "n2": "n22"})
        self.assertEqual([n["node_id"] for n in decision.plan.get("nodes")], ["n21", "n22"])
        self.assertEqual(decision.plan.get("nodes")[0]["action"], "hover('n1')")
        self.assertEqual(
            decision.plan.get("edges"),
            [{"from": "n21", "to": "n22", "precondition": "exists_content('n1')"}],
        )
        self.assertEqual(graph, original)
        self.assertFalse(decision.plan.get("next_step_decision")["should_plan_next"])

    def test_cumulative_verified_prefix_is_removed_without_changing_history(self):
        previous = plan([node("n3"), node("n8")], [{"from": "n3", "to": "n8"}])
        graph = plan(
            previous.to_dict()["nodes"] + [node("n20", "hover(target_id)")],
            previous.to_dict()["edges"] + [{"from": "n8", "to": "n20"}],
        )
        decision = manage_skill_plan(graph.to_symbolic(), previous, "None")
        self.assertEqual(decision.skipped_prefix_node_ids, ["n3", "n8"])
        self.assertEqual(decision.plan.get("nodes"), [node("n20", "hover(target_id)")])
        self.assertEqual(decision.plan.get("edges"), [])
        self.assertEqual(len(previous.get("nodes")), 2)

    def test_alias_ids_and_non_numeric_ids_are_canonicalized(self):
        graph = {
            "nodes": [
                {"id": "first", "action": "click(target_id)"},
                {"id": "second", "action": "hover(target_id)"},
            ],
            "edges": [{"from": "first", "to": "second"}],
        }
        decision = manage_skill_plan(json.dumps(graph), Plan.init_empty(), None)
        self.assertEqual([n["node_id"] for n in decision.plan.get("nodes")], ["n1", "n2"])
        self.assertEqual(decision.plan.get("edges"), [{"from": "n1", "to": "n2"}])

    def test_gapped_ids_remain_valid_and_unchanged(self):
        graph = plan([node("n3"), node("n8"), node("n20")])
        decision = manage_skill_plan(graph.to_symbolic(), Plan.init_empty(), None)
        self.assertEqual(decision.plan.to_dict(), graph.to_dict())
        self.assertEqual(decision.planner_node_mapping, {})

    def test_all_prefix_becomes_empty_plan_with_original_completion_decision(self):
        graph = plan([node("n3")])
        decision = manage_skill_plan(graph.to_symbolic(), graph, None)
        self.assertEqual(decision.plan.get("nodes"), [])
        self.assertFalse(decision.plan.get("next_step_decision")["should_plan_next"])

    def test_ambiguous_duplicate_ids_and_dangling_edges_are_rejected(self):
        for graph in (
            plan([node("n1"), node("n1")]),
            plan([node("n1")], [{"from": "n1", "to": "missing"}]),
            plan([node("n1"), node("n2")], [{"from": "n2", "to": "n1"}]),
        ):
            with self.subTest(graph=graph.to_dict()), self.assertRaises(ValueError):
                manage_skill_plan(graph.to_symbolic(), Plan.init_empty(), None)


class RepairIdentityTests(unittest.TestCase):
    def test_unknown_id_falls_back_to_first_uncommitted_node_and_correct_checkpoint(self):
        current = plan([node("n3"), node("n8"), node("n20")])
        committed = plan([node("n3")])
        model = QueueModel([FAIL, repair("n1", "n1")])
        agent, result = assess(model, current, committed)
        self.assertEqual(result.error_node, "n8")
        self.assertEqual(result.repair_signal["arguments"]["node_id"], "n8")
        self.assertEqual(result.checkpoint, "c1")
        self.assertEqual(agent.last_node_resolution["reason"], "first_uncommitted_node")
        self.assertIn("error_node: n8", model.prompts[1])
        self.assertIn("current plan IDs: ['n8', 'n20']", model.prompts[1])

    def test_valid_argument_or_formatted_reference_resolves_without_first_node_guess(self):
        current = plan([node("n3"), node("n8")])
        for error, argument in (("missing", "n8"), ("N008", "'n8'"), (8, None)):
            with self.subTest(error=error, argument=argument):
                _, result = assess(QueueModel([FAIL, repair(error, argument)]), current)
                self.assertEqual(result.error_node, "n8")
                self.assertEqual(result.checkpoint, "c1")

    def test_conflicting_valid_targets_are_rejected(self):
        with self.assertRaisesRegex(InvalidAssessment, "different current nodes"):
            assess(QueueModel([FAIL, repair("n3", "n8")]), plan([node("n3"), node("n8")]))

    def test_fallback_never_selects_an_already_committed_plan(self):
        current = plan([node("n3")])
        model = QueueModel([FAIL])
        with self.assertRaisesRegex(InvalidAssessment, "no uncommitted"):
            assess(model, current, current)
        self.assertEqual(len(model.prompts), 1)


class ExecutionCommitIdentityTests(unittest.TestCase):
    def test_recorded_264_collision_keeps_all_three_actions_in_three_plan_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            memory = ActiveMemory()
            prefix = node("n1", "click('642')")
            subgoal = SubgoalState(
                "Open target", "", True, plan=plan([prefix]), actions=["click('642')"]
            )
            task = SimpleNamespace(query="Open target", steps=0, plan=Plan.init_empty())
            pointer = RollBackPointer(browser.url)
            pointer.action_buffer = ["click('642')"]
            store = ArtifactStore(Path(directory) / "results", 264, "map")
            agent = Mock()
            execution = ExecutionService(browser, agent, memory, {}, store)
            repairs = RepairService(browser, memory, pointer, store)
            for old_id, new_id, action in (
                ("n1", "n2", "hover('644')"),
                ("n2", "n3", "hover('695')"),
            ):
                decision = manage_skill_plan(
                    plan([node(old_id, action)]).to_symbolic(), subgoal.plan, None
                )
                self.assertEqual(decision.planner_node_mapping, {old_id: new_id})
                agent.execute.return_value = bound((new_id, action))
                current = PlanState(decision.plan, "")
                batch = execution.execute(task, subgoal, current)
                current.complete = True
                repairs.commit(subgoal, current, batch)
            self.assertEqual([n["node_id"] for n in subgoal.plan.get("nodes")], ["n1", "n2", "n3"])
            self.assertEqual([n["action"] for n in subgoal.plan.get("nodes")], subgoal.actions)
            self.assertEqual(subgoal.actions, ["click('642')", "hover('644')", "hover('695')"])
            self.assertEqual(pointer.action_buffer, subgoal.actions)

    def test_repeated_executor_prefix_is_skipped_and_all_commit_traces_stay_aligned(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            memory = ActiveMemory()
            current = PlanState(
                plan(
                    [node("n3"), node("n8", "memorize(result_id)"), node("n20", "hover(target_id)")]
                ),
                "",
            )
            task = SimpleNamespace(query="Open and retain target", steps=0, plan=Plan.init_empty())
            subgoal = SubgoalState("Open target", "", True)
            agent = Mock()
            agent.execute.side_effect = [
                bound(("n3", "click('10')")),
                bound(("n3", "click('10')"), ("n8", "memorize('20')"), ("n20", "hover('21')")),
            ]
            store = ArtifactStore(Path(directory) / "results", 261, "map")
            pointer = RollBackPointer(browser.url)
            execution = ExecutionService(browser, agent, memory, {}, store)
            repairs = RepairService(browser, memory, pointer, store)
            first = execution.execute(task, subgoal, current)
            repairs.commit(subgoal, current, first)
            second = execution.execute(task, subgoal, current)
            self.assertEqual(second.node_ids, ["n8", "n20"])
            self.assertEqual(second.browser_node_ids, ["n20"])
            self.assertEqual(second.memory_pairs, [("n8", "20")])
            self.assertEqual(len(second.observations), 3)
            self.assertNotIn("n3:", second.filled)
            binding = json.loads(agent.execute.call_args.kwargs["plan_to_process"])
            self.assertEqual(binding["committed_node_ids"], ["n3"])
            current.complete = True
            repairs.commit(subgoal, current, second)
            before = (
                subgoal.plan.to_dict(),
                list(subgoal.actions),
                list(subgoal.observations),
                list(pointer.action_buffer),
            )
            repairs.commit(subgoal, current, second)
            self.assertEqual(
                before,
                (
                    subgoal.plan.to_dict(),
                    subgoal.actions,
                    subgoal.observations,
                    pointer.action_buffer,
                ),
            )
            self.assertEqual(browser.actions.count("click('10')"), 1)
            self.assertEqual(len(subgoal.plan.get("nodes")), 3)
            self.assertEqual(len(subgoal.actions), 3)
            self.assertIn("Grounded answer", memory.to_prompt())

    def test_changed_committed_node_is_blocked_before_browser_or_model_calls(self):
        browser, agent = Mock(), Mock()
        browser.get_page_state.return_value = {"url": "http://example.test", "axtree_object": ""}
        subgoal = SubgoalState("Open target", "", True, plan=plan([node("n1")]))
        service = ExecutionService(
            browser, agent, ActiveMemory(), {}, ArtifactStore(".", 261, "map")
        )
        with self.assertRaisesRegex(ValueError, "already committed"):
            service.execute(
                SimpleNamespace(query="q", steps=0),
                subgoal,
                PlanState(plan([node("n1", "hover(target_id)")]), ""),
            )
        agent.execute.assert_not_called()
        browser.execute_browsergym_action.assert_not_called()

    def test_executor_prompt_example_uses_pending_node_id(self):
        model = QueueModel(["result"])
        graph = plan([node("n3"), node("n8")]).to_executor_plan_obj()
        graph["committed_node_ids"] = ["n3"]
        Executor(model, "").execute(
            query="q",
            subgoal="g",
            axtree="",
            memory="",
            account_information={},
            plan_to_process=json.dumps(graph),
        )
        self.assertIn("'''n8: click('123')'''", model.prompts[0])
        self.assertNotIn("'''n1:", model.prompts[0])
