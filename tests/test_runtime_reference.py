"""Compare rollback, plan projection and protocol behavior to original outputs."""

import ast
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from skilltracer.agents.repair_parsing import parse_repair
from skilltracer.agents.summarizer import Summarizer
from skilltracer.planning.actions import ActionRegistry
from skilltracer.planning.checkpoints import CheckPointManager, RollBackPointer
from skilltracer.planning.plan import Plan, concat_plans
from skilltracer.runtime.results import extract_response

REFERENCE = json.loads((Path(__file__).parent / "fixtures/runtime_reference.json").read_text())


class RuntimeReferenceTests(unittest.TestCase):
    def test_rollback_states_and_programs_match_30_original_operations(self):
        for case in REFERENCE["rollback"]:
            pointer = RollBackPointer(case["initial_url"])
            for index, operation in enumerate(case["operations"]):
                with self.subTest(initial=case["initial_url"], operation=index):
                    result = getattr(pointer, operation["method"])(
                        *copy.deepcopy(operation["args"])
                    )
                    self.assertEqual(result, operation["result"])
                    self.assertEqual(vars(pointer), operation["state"])

    def test_executor_projection_and_plan_slices_match_original(self):
        for case in REFERENCE["plans"]:
            value = Plan(copy.deepcopy(case["plan"]))
            executor = {key: item for key, item in case["executor"].items() if key != "edges"}
            self.assertEqual(value.to_executor_plan_obj(), executor)
            self.assertEqual(json.loads(value.to_executor_plan_symbolic()), executor)
            for segment in case["slices"]:
                before, after = value.slice_before_node(segment["node_id"])
                self.assertEqual(before.to_dict(), segment["before"])
                self.assertEqual(after.to_dict(), segment["after"])
                if after.get("nodes"):
                    after.get("nodes")[0]["description"] = "Mutated copy"
                    self.assertEqual(value.to_dict(), case["plan"])

    def test_concatenation_preserves_original_edges_and_fallback_metadata(self):
        for case in REFERENCE["concatenation"]:
            result = concat_plans(Plan(case["left"]), Plan(case["right"]))
            self.assertEqual(result.to_dict(), case["expected"])

    def test_checkpoint_and_observation_trajectories_match_original(self):
        for case in REFERENCE["trajectories"]:
            manager = CheckPointManager(case["plan"])
            trajectory = manager.build_delta_checkpoint_action_traj(
                "[10] Start", case["actions"], case["deltas"], case["node_ids"]
            )
            self.assertEqual(manager.traj_to_prompt(trajectory), case["trajectory_text"])
            self.assertEqual(
                manager.checkpoint_node_traj_to_prompt(manager.build_checkpoint_node_traj()),
                case["checkpoint_text"],
            )

    def test_all_six_flag_combinations_choose_the_original_repair_branch(self):
        manager = CheckPointManager({"nodes": [{"node_id": "n8"}]})
        for case in REFERENCE["judge_flags"]:
            with self.subTest(flags=case["flags"]):
                responses = ["\n".join(str(value).lower() for value in case["flags"].values())]
                responses.append(
                    json.dumps(
                        {
                            "error_node": "n8",
                            "repair_signal": {
                                "tool": "REPLACE_ACTION",
                                "arguments": {"node_id": "n8", "new_action": "click(alternate_id)"},
                            },
                        }
                    )
                )
                model = Mock(side_effect=responses)
                result = Summarizer(model).summarize(
                    query="Find target",
                    subgoal="Open target",
                    plan="plan",
                    delta_axtree_traj="trajectory",
                    executed_actions="actions",
                    executed_actions_list=[],
                    executed_nodes_list="",
                    active_memory="",
                    short_term_memory="",
                    long_term_memory="",
                    checkpoints=manager,
                    last_checkpoint="c1",
                )
                self.assertEqual(result.plan_success, not case["trigger_repair"])
                self.assertEqual(model.call_count, 2 if case["trigger_repair"] else 1)

    def test_json_and_line_repairs_match_17_original_cases(self):
        for index, case in enumerate(REFERENCE["repairs"]):
            with self.subTest(case=index):
                result, guidance = parse_repair(case["text"])
                self.assertEqual(
                    {key: result.get(key) for key in case["expected"]}, case["expected"]
                )
                self.assertEqual(guidance, case["guidance"])

    def test_action_and_memory_parsing_match_original(self):
        for case in REFERENCE["action_parsing"]:
            browser, browser_ids, memory, memory_ids = ActionRegistry.executor_output_to_actions(
                case["text"]
            )
            full, full_ids = ActionRegistry.executor_output_to_full_actions(case["text"])
            self.assertEqual(
                [browser, browser_ids, memory, memory_ids, full, full_ids],
                [
                    case[key]
                    for key in (
                        "browser_actions",
                        "browser_node_ids",
                        "memory_actions",
                        "memory_node_ids",
                        "full_actions",
                        "full_node_ids",
                    )
                ],
            )

    def test_all_user_messages_are_joined_in_original_order(self):
        for case in REFERENCE["responses"]:
            # Infeasibility reports now contribute their reason to the final response.
            expected = (
                "No target"
                if case["actions"] == ["report_infeasible('No target')", "click('1')"]
                else case["expected"]
            )
            self.assertEqual(extract_response(case["actions"]), expected)

    def test_rollback_quotes_arbitrary_urls_as_python_literals(self):
        url = "http://test/items?name=Bob's"
        pointer = RollBackPointer(url)
        call = ast.parse(pointer.build_rollback_actions(0, [])[0], mode="eval").body
        self.assertEqual(ast.literal_eval(call.args[0]), url)


if __name__ == "__main__":
    unittest.main()
