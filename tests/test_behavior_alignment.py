"""Verify cross-layer contracts at execution, rollback, learning and storage boundaries."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_workflow import FakeBrowser

from skilltracer.agents.summarizer import Assessment
from skilltracer.memory.active import ActiveMemory
from skilltracer.memory.experience import LongTermMemory
from skilltracer.memory.skills import SkillRepository
from skilltracer.memory.subgoals import SubgoalMemory
from skilltracer.planning.checkpoints import CheckPointManager, RollBackPointer
from skilltracer.planning.plan import Plan
from skilltracer.runtime.execution import ExecutionService
from skilltracer.runtime.learning import LearningService
from skilltracer.runtime.repair import RepairService
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.results import ArtifactStore
from skilltracer.runtime.state import PlanState, SubgoalState


def plan(*ids):
    return Plan(
        {
            "nodes": [
                {"node_id": node, "action": "click(target_id)", "description": "Before patch"}
                for node in ids
            ],
            "edges": [
                {"from": left, "to": right, "precondition": "exists_content('target')"}
                for left, right in zip(ids, ids[1:])
            ],
            "next_step_decision": {"should_plan_next": False},
        }
    )


def bound(*pairs):
    return (
        "<plan components>:\n"
        + "\n".join(f"'''{node}: {action}'''" for node, action in pairs)
        + "\n<context>: Observed"
    )


class BehaviorAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.browser = FakeBrowser()
        self.memory = ActiveMemory()
        self.agent = Mock()
        self.store = ArtifactStore(Path(self.temp.name), 1, "map")
        self.task = SimpleNamespace(
            query="Open target", steps=0, long_term=[], plan=Plan.init_empty()
        )
        self.subgoal = SubgoalState("Open target", "", True)
        self.pointer = RollBackPointer(self.browser.url)
        self.execution = ExecutionService(self.browser, self.agent, self.memory, {}, self.store)
        self.repairs = RepairService(self.browser, self.memory, self.pointer, self.store)

    def run_batch(self, current, *pairs):
        self.agent.execute.return_value = bound(*pairs)
        return self.execution.execute(self.task, self.subgoal, current)

    def repair(self, current, batch, node, action="click(alternative_id)"):
        assessment = Assessment(
            False,
            "c0",
            error_node=node,
            repair_signal={
                "tool": "REPLACE_ACTION",
                "arguments": {"node_id": node, "new_action": action},
            },
            insight="Use the observed alternative",
        )
        return self.repairs.apply(self.task, self.subgoal, current, batch, assessment)

    def test_executor_cannot_skip_unfinished_frontier(self):
        with self.assertRaisesRegex(ValueError, "prefix|pending|frontier"):
            self.run_batch(PlanState(plan("n3", "n8"), ""), ("n8", "click('10')"))
        self.assertEqual(self.browser.actions, [])

    def test_failed_browser_action_stops_all_later_effects_and_keeps_trace_aligned(self):
        current = PlanState(plan("n3", "n8", "n20"), "")
        current.plan.get_node_by_id("n20")["action"] = "memorize(result_id)"
        batch = self.run_batch(
            current, ("n3", "click('bad')"), ("n8", "click('10')"), ("n20", "memorize('20')")
        )
        self.assertEqual(self.browser.actions, ["click('bad')"])
        self.assertEqual(batch.node_ids, ["n3"])
        self.assertEqual(batch.actions, ["click('bad')"])
        self.assertEqual(len(batch.observations), 2)
        self.assertEqual(len(batch.browser_observations), 2)
        self.assertEqual(batch.memory_pairs, [])
        self.assertNotIn("n8:", batch.filled)
        self.assertEqual(self.memory.to_dict(), {})

    def test_retry_without_new_progress_does_not_promote_long_term_memory(self):
        current = PlanState(plan("n3", "n8"), "")
        first = self.run_batch(current, ("n3", "click('10')"))
        self.repairs.commit(self.subgoal, current, first)
        for _ in range(2):
            batch = self.run_batch(current, ("n8", "click('bad')"))
            self.repair(current, batch, "n8")
        self.assertEqual(len(self.task.long_term), 0)
        self.assertEqual(len(self.subgoal.failures), 1)
        self.assertTrue(self.subgoal.failures[0].usable)

    def test_promoted_memory_records_previous_repair_that_enabled_progress(self):
        current = PlanState(plan("n3", "n8"), "")
        first = self.run_batch(current, ("n3", "click('bad')"))
        self.repair(current, first, "n3", "click(first_fix_id)")
        second = self.run_batch(current, ("n3", "click('10')"), ("n8", "click('bad')"))
        self.repair(current, second, "n8", "click(second_fix_id)")
        self.assertEqual(len(self.task.long_term), 1)
        tool = self.task.long_term[0].success_repair["repair_tool"]
        self.assertEqual(tool["arguments"]["new_action"], "click(first_fix_id)")

    def test_failure_memory_is_snapshot_before_action_patch(self):
        current = PlanState(plan("n3"), "")
        batch = self.run_batch(current, ("n3", "click('bad')"))
        self.repair(current, batch, "n3")
        self.assertEqual(
            self.subgoal.failures[0].original_failed_node["action"], "click(target_id)"
        )
        self.assertIsNot(
            self.subgoal.failures[0].original_failed_node, current.plan.get_node_by_id("n3")
        )

    def test_rollback_restores_overwritten_memory_instead_of_deleting_previous_evidence(self):
        self.memory.memorize("10", "[10] link 'Original evidence'")
        original = self.memory.to_dict()
        self.browser.tree = "[10] link 'New evidence'"
        current = PlanState(plan("n3", "n8"), "")
        current.plan.get_node_by_id("n3")["action"] = "memorize(result_id)"
        batch = self.run_batch(current, ("n3", "memorize('10')"), ("n8", "click('bad')"))
        self.repair(current, batch, "n3")
        self.assertEqual(self.memory.to_dict(), original)

    def test_ordinary_commits_keep_internal_conditions_across_executor_rounds(self):
        current = PlanState(plan("n3", "n8"), "")
        current.plan.get_node_by_id("n8")["action"] = "hover(target_id)"
        for node, action in (("n3", "click('10')"), ("n8", "hover('20')")):
            batch = self.run_batch(current, (node, action))
            self.repairs.commit(self.subgoal, current, batch)
        self.assertEqual(self.subgoal.plan.get("edges"), current.plan.get("edges"))

    def test_skill_bank_roundtrip_preserves_literal_fences_quotes_and_backslashes(self):
        skill = {
            "id": "s1",
            "name": "Literal evidence",
            "description": "Keep '''skill {raw} and ``` fences, \"quotes\" C:\\new",
            "plan": plan("n3").to_dict(),
        }
        repository = SkillRepository(str(Path(self.temp.name) / "skills.txt"))
        repository.write_back([skill])
        self.assertEqual(repository.read_all(), [skill])

    def test_executor_cannot_change_a_planned_primitive(self):
        with self.assertRaisesRegex(ValueError, "primitive"):
            self.run_batch(PlanState(plan("n3"), ""), ("n3", "hover('10')"))
        self.assertEqual(self.browser.actions, [])

    def test_invalid_replacement_action_is_rejected_before_rollback_or_memory_updates(self):
        from skilltracer.agents.summarizer import InvalidAssessment

        current = PlanState(plan("n3"), "")
        batch = self.run_batch(current, ("n3", "click('bad')"))
        count = len(self.browser.actions)
        with self.assertRaises(InvalidAssessment):
            self.repair(current, batch, "n3", "not_a_call")
        self.assertEqual(len(self.browser.actions), count)
        self.assertEqual(self.subgoal.failures, [])

    def test_unknown_primitive_is_rejected_before_browser_effects(self):
        current = PlanState(plan("n3"), "")
        current.plan.get_node_by_id("n3")["action"] = "invented_action(target_id)"
        with self.assertRaisesRegex(ValueError, "primitive"):
            self.run_batch(current, ("n3", "invented_action('10')"))
        self.assertEqual(self.browser.actions, [])

    def test_executor_condition_claims_do_not_control_runtime(self):
        current = PlanState(plan("n3", "n8"), "")
        self.agent.execute.return_value = "<plan components>:\n'''n3: click('10')'''\n'''c: exists_content('missing') = false'''\n'''n8: click('20')'''\n<context>: Observed"
        batch = self.execution.execute(self.task, self.subgoal, current)
        self.assertEqual(batch.node_ids, ["n3", "n8"])
        self.assertEqual(self.browser.actions, ["click('10')", "click('20')"])
        self.assertNotIn("c:", batch.filled)
        self.assertEqual(batch.edge_results[0]["condition"], "exists_content('target')")

    def test_assessment_starts_from_browser_observation_instead_of_model_context(self):
        current = PlanState(plan("n3"), "")
        self.agent.execute.return_value = (
            "<plan components>:\n'''n3: click('10')'''\n<context>: Invented page content"
        )
        batch = self.execution.execute(self.task, self.subgoal, current)
        trace = self.execution.describe(batch, CheckPointManager(current.plan.to_dict()))
        self.assertIn("[AXTREE_0]\n[10] link 'Target'", trace)
        self.assertNotIn("Invented page content", trace)

    def test_plan_completion_fields_require_real_booleans(self):
        from skilltracer.planning.plan import InvalidPlan

        for value in ("false", 0, None, {}):
            with self.subTest(value=value):
                graph = plan("n3").to_dict()
                graph["next_step_decision"]["should_plan_next"] = value
                with self.assertRaises(InvalidPlan):
                    Plan.from_symbolic(Plan(graph).to_symbolic())

    def test_unknown_purpose_is_rejected_before_learning_or_execution(self):
        graph = plan("n3").to_dict()
        graph["nodes"][0]["purpose"] = "observation"
        with self.assertRaisesRegex(ValueError, "semantic stage"):
            Plan.from_symbolic(Plan(graph).to_symbolic())

    def test_malformed_stored_skill_is_not_misclassified_as_retryable_model_plan(self):
        import json

        from skilltracer.planning.plan import InvalidPlan
        from skilltracer.planning.reuse import manage_skill_plan

        skill = {
            "id": "s1",
            "plan": {"nodes": [], "next_step_decision": {"should_plan_next": "false"}},
        }
        with self.assertRaisesRegex(ValueError, "Injected skill graph") as raised:
            manage_skill_plan(
                json.dumps({"use_skill": True, "nodes": []}), Plan.init_empty(), json.dumps(skill)
            )
        self.assertNotIsInstance(raised.exception, InvalidPlan)

    def test_reuser_cannot_select_a_skill_outside_presented_candidates(self):
        repositories = Repositories.open(Path(self.temp.name) / "state", "map")
        skills = [
            {
                "id": "s1",
                "name": "Open target",
                "description": "Open target",
                "plan": plan("n3").to_dict(),
            },
            {
                "id": "s2",
                "name": "Other",
                "description": "Completely unrelated words",
                "plan": plan("n3").to_dict(),
            },
        ]
        repositories.skills.write_back(skills)
        task = repositories.load_task("Open target", 3)
        reuser = Mock()
        reuser.reuse.return_value = (True, "s2")
        selected = LearningService(repositories, reuser, Mock()).select_skill(task, "Open target")
        self.assertEqual(selected, "None")
        self.assertEqual([s["id"] for s in reuser.reuse.call_args.args[2]], ["s1"])

    def test_long_term_jsonl_preserves_structured_evidence(self):
        path = str(Path(self.temp.name) / "memory.txt")
        item = LongTermMemory(
            "Task", "Goal", {"node_id": "n3"}, {"evidence": 'Literal "quote" C:\\new'}, [], {}
        )
        LongTermMemory.import_long_term_memories_to_txt([item], path)
        restored = LongTermMemory.export_long_term_memories_from_txt(path)
        self.assertEqual(restored[0].to_dict(), item.to_dict())

    def test_bank_serialization_failure_does_not_truncate_previous_data(self):
        path = Path(self.temp.name) / "memory.txt"
        path.write_text("original\n")
        invalid = LongTermMemory("Task", "Goal", {}, "", [], {"invalid": {1, 2}})
        with self.assertRaises(TypeError):
            LongTermMemory.import_long_term_memories_to_txt([invalid], str(path))
        self.assertEqual(path.read_text(), "original\n")
        repository = SubgoalMemory(str(path))
        with self.assertRaises(TypeError):
            repository._dump_jsonl([{"invalid": {1, 2}}])
        self.assertEqual(path.read_text(), "original\n")
