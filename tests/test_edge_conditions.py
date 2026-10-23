"""Check deterministic page matching and delivery through the existing assessment input."""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_workflow import SUCCESS_FLAGS, FakeBrowser, QueueModel

from skilltracer.agents.executor import Executor
from skilltracer.agents.summarizer import Summarizer
from skilltracer.memory.active import ActiveMemory
from skilltracer.planning.checkpoints import CheckPointManager, RollBackPointer
from skilltracer.planning.conditions import check_condition, check_incoming_edges
from skilltracer.planning.plan import Plan
from skilltracer.runtime.execution import ExecutionService
from skilltracer.runtime.repair import RepairService
from skilltracer.runtime.results import ArtifactStore
from skilltracer.runtime.state import PlanState, SubgoalState


def graph(condition="exists_content('Grounded answer')"):
    return Plan(
        {
            "nodes": [
                {"node_id": "n3", "action": "click(target_id)"},
                {"node_id": "n8", "action": "hover(target_id)"},
            ],
            "edges": [{"from": "n3", "to": "n8", "preconditions": [condition]}],
            "next_step_decision": {"should_plan_next": False},
        }
    )


def bound(*pairs):
    return (
        "<plan components>:\n"
        + "\n".join(f"'''{node}: {action}'''" for node, action in pairs)
        + "\n<context>: Observed"
    )


class ConditionMatchTests(unittest.TestCase):
    tree = "[1] RootWebArea 'Checkout', url='https://test/?value=secret'\n\t[2] textbox 'Email' value=\"Bob's offer\"\n\t[3] button 'SAVE   ORDER', description='Finish checkout'\n\tStaticText 'Thank\\tyou'\n\t[4] link 'Fullwidth ＡＢＣ'\n\t[5] checkbox 'Choice' checked=True"

    def test_component_is_normalized_exact_role_match(self):
        for expression in (
            "exists_component('textbox')",
            "exists_component(component_type=' TEXTBOX ')",
        ):
            result = check_condition(expression, self.tree)
            self.assertTrue(result["result"])
            self.assertEqual(result["evidence"][0]["bid"], "2")
        for role in ("text", "Email", "secret", "2"):
            self.assertFalse(check_condition(f"exists_component({role!r})", self.tree)["result"])

    def test_content_uses_normalized_substring_in_text_fields(self):
        for text in ("save order", "BOB'S OFFER", "thank you", "abc", "Finish checkout"):
            with self.subTest(text=text):
                result = check_condition(f"exists_content(target_content={text!r})", self.tree)
                self.assertTrue(result["result"])
                self.assertIn("field", result["evidence"][0])

    def test_content_does_not_match_roles_ids_urls_or_join_different_nodes(self):
        for text in ("textbox", "secret", "1", "Email SAVE", "order save"):
            with self.subTest(text=text):
                self.assertFalse(check_condition(f"exists_content({text!r})", self.tree)["result"])

    def test_literals_survive_quotes_backslashes_unicode_and_fences(self):
        for value in ['Bob\'s "offer"', r"C:\Users\new", "literal ''' and ``` {raw}", "café 🧭"]:
            with self.subTest(value=value):
                tree = f"[10] StaticText {value!r}"
                result = check_condition(f"exists_content({value!r})", tree)
                self.assertTrue(result["result"])
                self.assertEqual(result["evidence"][0]["value"], value)
                self.assertEqual(json.loads(json.dumps(result)), result)

    def test_unsupported_or_nonliteral_conditions_are_reported_without_execution(self):
        for condition in [
            "exists_content(variable)",
            "unknown('x')",
            "exists_content('')",
            "exists_content('a') or exists_content('b')",
            "__import__('os').getcwd()",
            "exists_component(other='button')",
            None,
        ]:
            with self.subTest(condition=condition):
                result = check_condition(condition, self.tree)
                self.assertFalse(result["result"])
                self.assertIn("error", result)

    def test_both_legacy_condition_fields_and_unconditional_edges(self):
        observation = {"url": "http://test", "axtree_object": self.tree}
        for field, value in [
            ("precondition", "exists_component('button')"),
            ("preconditions", ["exists_component('button')"]),
        ]:
            result = check_incoming_edges(
                Plan({"edges": [{"from": "n3", "to": "n8", field: value}]}),
                "n8",
                {"n3"},
                observation,
            )
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0]["result"])
        self.assertEqual(
            check_incoming_edges(
                Plan({"edges": [{"from": "n3", "to": "n8"}]}), "n8", {"n3"}, observation
            ),
            [],
        )
        self.assertEqual(check_incoming_edges(graph(), "n8", set(), observation), [])


class EdgeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.browser = FakeBrowser()
        self.memory = ActiveMemory()
        self.model = QueueModel([])
        self.executor = Executor(self.model, "click(bid), hover(bid)")
        self.store = ArtifactStore(Path(self.temp.name), 1, "map")
        self.service = ExecutionService(self.browser, self.executor, self.memory, {}, self.store)
        self.task = SimpleNamespace(query="Open target", steps=0, plan=Plan.init_empty())
        self.subgoal = SubgoalState("Open target", "", True)

    def test_executor_receives_no_edges_or_edge_instructions(self):
        self.model.responses = [bound(("n3", "click('10')"))]
        current = PlanState(graph(), "")
        self.service.execute(self.task, self.subgoal, current)
        prompt = self.model.prompts[0]
        for forbidden in ('"edges"', "precondition", "exists_content", "exists_component", "c:"):
            self.assertNotIn(forbidden, prompt)
        self.assertNotIn("edges", current.plan.to_executor_plan_obj())
        self.assertIn("edges", current.plan.to_dict())

    def test_direct_executor_calls_also_strip_graph_edges(self):
        self.model.responses = ["result"]
        self.executor.execute(
            query="q",
            subgoal="g",
            axtree="",
            memory="",
            account_information={},
            plan_to_process=json.dumps(graph().to_dict()),
        )
        self.assertNotIn('"edges"', self.model.prompts[0])

    def test_boundary_uses_observation_after_previous_node_inside_one_batch(self):
        self.model.responses = [bound(("n3", "click('10')"), ("n8", "hover('20')"))]
        batch = self.service.execute(self.task, self.subgoal, PlanState(graph(), ""))
        self.assertEqual(len(batch.edge_results), 1)
        result = batch.edge_results[0]
        self.assertTrue(result["result"])
        self.assertEqual(result["url"], "http://example.test/result")
        self.assertEqual(result["evidence"][0]["value"], "Grounded answer")

    def test_next_boundary_is_reported_when_executor_stops_after_source(self):
        self.model.responses = [bound(("n3", "click('10')"))]
        batch = self.service.execute(self.task, self.subgoal, PlanState(graph(), ""))
        self.assertEqual(batch.node_ids, ["n3"])
        self.assertEqual(
            [(r["from"], r["to"], r["result"]) for r in batch.edge_results], [("n3", "n8", True)]
        )

    def test_cross_batch_boundary_is_rechecked_on_latest_page(self):
        self.model.responses = [bound(("n3", "click('10')")), bound(("n8", "hover('20')"))]
        current = PlanState(graph(), "")
        first = self.service.execute(self.task, self.subgoal, current)
        RepairService(
            self.browser, self.memory, RollBackPointer(self.browser.url), self.store
        ).commit(self.subgoal, current, first)
        self.browser.tree = "[20] heading 'Different page'"
        second = self.service.execute(self.task, self.subgoal, current)
        self.assertTrue(first.edge_results[0]["result"])
        self.assertFalse(second.edge_results[0]["result"])
        self.assertEqual(second.node_ids, ["n8"])

    def test_nonadjacent_edge_waits_for_its_target_boundary(self):
        plan = graph()
        plan.obj["nodes"].insert(1, {"node_id": "n5", "action": "hover(target_id)"})
        self.model.responses = [
            bound(("n3", "click('10')")),
            bound(("n5", "hover('20')")),
        ]
        current = PlanState(plan, "")
        first = self.service.execute(self.task, self.subgoal, current)
        self.assertEqual(first.edge_results, [])
        RepairService(
            self.browser, self.memory, RollBackPointer(self.browser.url), self.store
        ).commit(self.subgoal, current, first)
        second = self.service.execute(self.task, self.subgoal, current)
        self.assertEqual(second.node_ids, ["n5"])
        self.assertEqual([(r["from"], r["to"]) for r in second.edge_results], [("n3", "n8")])

    def test_empty_binding_still_reports_the_reached_cross_batch_boundary(self):
        self.subgoal.plan, _ = graph().slice_before_node("n8")
        self.model.responses = [bound()]
        batch = self.service.execute(self.task, self.subgoal, PlanState(graph(), ""))
        self.assertEqual(batch.actions, [])
        self.assertEqual(len(batch.edge_results), 1)
        self.assertFalse(batch.edge_results[0]["result"])

    def test_false_result_is_evidence_only_and_does_not_add_an_execution_gate(self):
        self.model.responses = [bound(("n3", "click('10')"), ("n8", "hover('20')"))]
        batch = self.service.execute(
            self.task, self.subgoal, PlanState(graph("exists_component('textbox')"), "")
        )
        self.assertFalse(batch.edge_results[0]["result"])
        self.assertEqual(batch.node_ids, ["n3", "n8"])
        self.assertEqual(batch.errors, [])

    def test_failed_source_does_not_report_a_transition_that_was_never_reached(self):
        self.model.responses = [bound(("n3", "click('bad')"), ("n8", "hover('20')"))]
        batch = self.service.execute(self.task, self.subgoal, PlanState(graph(), ""))
        self.assertEqual(batch.node_ids, ["n3"])
        self.assertEqual(batch.edge_results, [])

    def test_results_reach_both_summarizer_stages_through_existing_trajectory_input(self):
        self.model.responses = [bound(("n3", "click('10')"))]
        current = PlanState(graph("exists_content('Missing literal')"), "")
        batch = self.service.execute(self.task, self.subgoal, current)
        response = "error_node: n8\nrepair_tool: REFINE_DESCRIPTION\nnode_id: n8\nnew_description: Locate the observed target\nsubgoal_guidance: Null"
        summary_model = QueueModel(["false\nfalse\nfalse\nfalse\nfalse\nfalse", response])
        agent = Summarizer(summary_model)
        trace = self.service.describe(batch, CheckPointManager(current.plan.to_dict()))
        result = agent.summarize(
            query="q",
            subgoal="g",
            plan=current.plan.to_symbolic(),
            delta_axtree_traj=trace,
            executed_actions=batch.filled,
            executed_actions_list=[],
            executed_nodes_list=Plan.init_empty().to_symbolic(),
            active_memory="",
            short_term_memory="",
            long_term_memory="",
            checkpoints=CheckPointManager(current.plan.to_dict()),
            last_checkpoint="c1",
        )
        self.assertEqual(len(summary_model.prompts), 2)
        self.assertFalse(result.plan_success)
        for prompt in summary_model.prompts:
            self.assertIn("[PROGRAM_EDGE_CHECKS]", prompt)
            self.assertIn('"result": false', prompt)
            self.assertIn('"to": "n8"', prompt)
        self.assertEqual(
            json.loads(trace.split("[PROGRAM_EDGE_CHECKS]\n", 1)[1]), batch.edge_results
        )
        self.assertNotIn("edge_results", inspect.signature(Summarizer.summarize).parameters)

    def test_false_edge_does_not_force_an_extra_summarizer_stage(self):
        self.model.responses = [bound(("n3", "click('10')"))]
        current = PlanState(graph("exists_content('Missing literal')"), "")
        batch = self.service.execute(self.task, self.subgoal, current)
        summary_model = QueueModel([SUCCESS_FLAGS])
        checkpoints = CheckPointManager(current.plan.to_dict())
        result = Summarizer(summary_model).summarize(
            query="q",
            subgoal="g",
            plan=current.plan.to_symbolic(),
            delta_axtree_traj=self.service.describe(batch, checkpoints),
            executed_actions=batch.filled,
            executed_actions_list=[],
            executed_nodes_list=Plan.init_empty().to_symbolic(),
            active_memory="",
            short_term_memory="",
            long_term_memory="",
            checkpoints=checkpoints,
            last_checkpoint="c1",
        )
        self.assertTrue(result.plan_success)
        self.assertEqual(len(summary_model.prompts), 1)
        self.assertIn('"result": false', summary_model.prompts[0])
