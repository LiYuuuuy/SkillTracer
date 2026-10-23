"""Exercise rollback with mixed memory/browser actions and repair boundaries."""

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_workflow import FakeBrowser

from skilltracer.agents.summarizer import Assessment
from skilltracer.memory.active import ActiveMemory
from skilltracer.planning.checkpoints import RollBackPointer
from skilltracer.planning.plan import Plan
from skilltracer.runtime.execution import ExecutionService
from skilltracer.runtime.repair import REPAIR_FIELDS, RepairService
from skilltracer.runtime.results import ArtifactStore
from skilltracer.runtime.state import PlanState, SubgoalState


class ReplayBrowser(FakeBrowser):
    def execute_browsergym_action(self, action):
        if action.startswith("goto("):
            self.url = ast.literal_eval(ast.parse(action, mode="eval").body.args[0])
            self.tree = "[10] link 'Target'"
        return super().execute_browsergym_action(action)


class RepairBoundaryTests(unittest.TestCase):
    def make_attempt(self, directory):
        browser = ReplayBrowser()
        memory = ActiveMemory()
        actions = [
            "memorize('10')",
            "click('10')",
            "memorize('20')",
            "click('bad')",
            "memorize('21')",
        ]
        ids = ["n3", "n8", "n12", "n20", "n99"]
        plan = Plan(
            {
                "nodes": [
                    {
                        "node_id": node_id,
                        "purpose": "navigation",
                        "component_type": "link",
                        "action": "memorize(result_id)"
                        if action.startswith("memorize(")
                        else "click(target_id)",
                        "description": "Retain the observed evidence.",
                    }
                    for node_id, action in zip(ids, actions)
                ],
                "edges": [
                    {
                        "from": ids[index],
                        "to": ids[index + 1],
                        "precondition": "exists_content('visible')",
                    }
                    for index in range(len(ids) - 1)
                ],
                "next_step_decision": {"should_plan_next": False},
            }
        )
        response = (
            "<plan components>:\n"
            + "\n".join(f"'''{node_id}: {action}'''" for node_id, action in zip(ids, actions))
            + "\n<context>:\n[10] link 'Target'"
        )
        agent = Mock()
        agent.execute.return_value = response
        store = ArtifactStore(Path(directory) / "results", 261, "map")
        service = ExecutionService(browser, agent, memory, {}, store)
        task = SimpleNamespace(query="Retain evidence and open target", steps=0, long_term=[])
        subgoal = SubgoalState("Open target", "", True)
        current = PlanState(plan, "[10] link 'Target'")
        pointer = RollBackPointer(browser.url)
        pointer.update_from_success_trace([browser.url], ["fill('10', 'existing')"])
        batch = service.execute(task, subgoal, current)
        return browser, memory, task, subgoal, current, pointer, batch, store

    def test_all_four_tools_preserve_prefix_and_remove_only_failed_suffix_memory(self):
        for tool, (tool_id, parameter, field) in REPAIR_FIELDS.items():
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as directory:
                browser, memory, task, subgoal, current, pointer, batch, store = self.make_attempt(
                    directory
                )
                self.assertEqual(set(memory.to_dict()), {"10", "20"})
                self.assertEqual(batch.node_ids[-1], "n20")
                signal = {
                    "tool": tool,
                    "arguments": {
                        "node_id": "n20",
                        parameter: {0: "filter", 2: "click(corrected_target_id)"}.get(
                            tool_id, "corrected_value"
                        ),
                    },
                }
                assessment = Assessment(
                    False,
                    "c3",
                    error_node="n20",
                    error_evidence={"evidence": "Element unavailable"},
                    repair_signal=signal,
                    insight="Use observed evidence.",
                )
                result = RepairService(browser, memory, pointer, store).apply(
                    task, subgoal, current, batch, assessment
                )
                self.assertEqual(result, tool_id > 1)
                self.assertEqual(set(memory.to_dict()), {"10", "20"})
                self.assertEqual(
                    subgoal.actions, ["memorize('10')", "click('10')", "memorize('20')"]
                )
                self.assertEqual(
                    [node["node_id"] for node in subgoal.plan.get("nodes")], ["n3", "n8", "n12"]
                )
                self.assertEqual(subgoal.plan.get("edges"), current.plan.get("edges")[:2])
                self.assertEqual(
                    browser.actions[-3:],
                    ["goto('http://example.test')", "fill('10', 'existing')", "click('10')"],
                )
                self.assertEqual(browser.url, "http://example.test/result")
                self.assertNotIn("n20:", current.filled if result else "")
                self.assertFalse(subgoal.last_plan_succeeded)
                self.assertIs(subgoal.past_plan, current.plan)
                self.assertIsNot(
                    subgoal.failures[0].original_failed_node, current.plan.get_node_by_id("n20")
                )
                self.assertEqual(
                    subgoal.failures[0].original_failed_node["action"], "click(target_id)"
                )
                if result:
                    self.assertEqual(
                        current.plan.get_node_by_id("n20")[field], signal["arguments"][parameter]
                    )
                    self.assertIn("downstream nodes unchanged", subgoal.planning_request)
                else:
                    self.assertIn("replan every downstream node", subgoal.planning_request)

    def test_repeated_failure_does_not_duplicate_already_committed_nodes_or_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            browser, memory, task, subgoal, current, pointer, batch, store = self.make_attempt(
                directory
            )
            repairs = RepairService(browser, memory, pointer, store)
            assessment = Assessment(
                False,
                "c3",
                error_node="n20",
                repair_signal={
                    "tool": "REPLACE_ACTION",
                    "arguments": {"node_id": "n20", "new_action": "click(alternate_id)"},
                },
                insight="Use the alternative.",
            )
            repairs.apply(task, subgoal, current, batch, assessment)
            batch.actions = ["click('bad')"]
            batch.node_ids = ["n20"]
            batch.browser_actions = ["click('bad')"]
            batch.browser_node_ids = ["n20"]
            batch.observations = batch.observations[-2:]
            batch.browser_observations = batch.browser_observations[-2:]
            batch.memory_pairs = []
            repairs.apply(task, subgoal, current, batch, assessment)
            self.assertEqual(len(subgoal.plan.get("nodes")), 3)
            self.assertEqual(len(subgoal.plan.get("edges")), 2)
            self.assertEqual(len(subgoal.actions), 3)


if __name__ == "__main__":
    unittest.main()
