"""Regress replacement input, evidence retention and infeasibility output."""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from browsergym.core.action.functions import fill
from test_workflow import SUCCESS_FLAGS, FakeBrowser, FakeEvaluator, QueueModel, plan_text

from skilltracer.memory.active import ActiveMemory, MemoryWrite
from skilltracer.planning.checkpoints import RollBackPointer
from skilltracer.planning.plan import Plan
from skilltracer.runtime.execution import ExecutionService
from skilltracer.runtime.repair import RepairService
from skilltracer.runtime.results import ArtifactStore, extract_response
from skilltracer.runtime.state import PlanState, SubgoalState
from skilltracer.runtime.workflow import Workflow


class InputField:
    """Model a widget that appends inserted text until explicitly cleared."""

    def __init__(self, tag="INPUT", kind="text", value="Existing", reject=False):
        self.tag, self.kind, self.value = tag, kind, value
        self.calls = []
        self.reject = reject

    def clear(self, **kwargs):
        self.calls.append("clear")
        self.value = ""

    def fill(self, value, **kwargs):
        self.calls.append("fill")
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        if self.tag == "INPUT":
            text = value.replace("\r", "").replace("\n", "")
            if self.kind in ("email", "url"):
                text = text.strip(" \t\f")
        self.value += "Rejected" if self.reject else text

    def type(self, value, **kwargs):
        self.calls.append("type")
        self.value += value

    def evaluate(self, expression):
        return {"tag": self.tag, "type": self.kind, "value": self.value}


class FillTests(unittest.TestCase):
    def apply(self, field, value, mode=None):
        scope = {
            "page": object(),
            "demo_mode": mode,
            "retry_with_force": False,
            "get_elem_by_bid": lambda *args: field,
            "add_demo_mode_effects": lambda *args, **kwargs: None,
            "call_fun": lambda function, retry: function(force=False),
        }
        exec(inspect.getsource(fill), scope)
        scope["fill"]("10", value)

    def test_prefilled_input_is_replaced_instead_of_appended(self):
        for mode in (None, "off", "default"):
            with self.subTest(mode=mode):
                field = InputField()
                self.apply(field, "Replacement", mode)
                self.assertEqual(field.value, "Replacement")
                self.assertEqual(field.calls[0], "clear")

    def test_empty_value_also_works_in_demo_mode(self):
        field = InputField()
        self.apply(field, "", "default")
        self.assertEqual(field.value, "")

    def test_literal_quotes_backslashes_and_unicode_survive(self):
        value = 'Bob\'s "offer" C:\\new 🧭'
        field = InputField()
        self.apply(field, value)
        self.assertEqual(field.value, value)

    def test_textarea_and_contenteditable_keep_multiline_values(self):
        for tag in ("TEXTAREA", "DIV"):
            field = InputField(tag=tag)
            self.apply(field, "line one\r\nline two")
            self.assertEqual(field.value, "line one\nline two")

    def test_input_email_sanitization_does_not_report_a_false_failure(self):
        field = InputField(kind="email")
        self.apply(field, " user@example.com \r\n")
        self.assertEqual(field.value, "user@example.com")

    def test_mismatched_readback_is_an_error_and_does_not_expose_input(self):
        field = InputField(reject=True)
        with self.assertRaisesRegex(ValueError, "requested field value") as error:
            self.apply(field, "sensitive-value")
        self.assertNotIn("sensitive-value", str(error.exception))

    def test_nonstring_value_is_rejected_before_clearing(self):
        field = InputField()
        with self.assertRaisesRegex(ValueError, "string"):
            self.apply(field, 123)
        self.assertEqual(field.value, "Existing")
        self.assertEqual(field.calls, [])


class MemoryRecordTests(unittest.TestCase):
    def test_reused_bid_keeps_both_pages_and_sources(self):
        memory = ActiveMemory()
        memory.memorize("1536", "[1536] list '$35 $149'", source_url="http://test/?p=1")
        memory.memorize("1536", "[1536] list '$9.99'", source_url="http://test/?p=2")
        self.assertEqual(set(memory.to_dict()), {"1536", "1536@2"})
        for text in ("$35", "$149", "$9.99", "http://test/?p=1", "http://test/?p=2"):
            self.assertIn(text, memory.to_prompt())
        self.assertIn("$9.99", memory.get_memory_by_bid("1536"))

    def test_dynamic_content_at_the_same_url_keeps_both_versions(self):
        memory = ActiveMemory()
        for text in ("Before", "After"):
            memory.memorize("10", f"[10] heading '{text}'", source_url="http://test/spa")
        self.assertEqual(len(memory.to_dict()), 2)
        self.assertIn("Before", memory.to_prompt())
        self.assertIn("After", memory.to_prompt())

    def test_failed_capture_does_not_create_a_record(self):
        memory = ActiveMemory()
        for bid in ("Null", "missing"):
            self.assertIsNone(memory.memorize(bid, "[10] link 'Observed'"))
        self.assertEqual(memory.to_dict(), {})

    def test_undo_removes_only_the_produced_record_and_ids_are_not_reused(self):
        memory = ActiveMemory()
        memory.memorize("10", "[10] heading 'Original'")
        original = memory.to_dict()
        memory.memorize("10", "[10] heading 'Suffix'")
        undone = memory.latest_record_id("10")
        self.assertTrue(memory.discard_record(undone))
        self.assertEqual(memory.to_dict(), original)
        memory.memorize("10", "[10] heading 'Retry'")
        self.assertNotEqual(memory.latest_record_id("10"), undone)
        self.assertFalse(memory.discard_record(undone))
        self.assertIn("Retry", memory.get_memory_by_bid("10"))

    def test_legacy_content_restore_and_flat_serialization_remain_supported(self):
        memory = ActiveMemory()
        previous = memory.memorize("10", "[10] heading 'Previous'")
        original = memory.to_dict()
        memory.memorize("10", "[10] heading 'New'")
        memory.restore_memory_by_bid("10", previous)
        self.assertEqual(json.loads(json.dumps(memory.to_dict())), original)

    def test_runtime_rollback_keeps_prefix_and_older_pages_with_the_same_bid(self):
        class Browser(FakeBrowser):
            def execute_browsergym_action(self, action):
                result = super().execute_browsergym_action(action)
                if action == "click('10')":
                    self.url = "http://example.test/page2"
                    self.tree = "[10] heading 'Page two'"
                return result

        browser, memory, agent = Browser(), ActiveMemory(), Mock()
        pairs = [
            ("n3", "memorize('10')"),
            ("n5", "click('10')"),
            ("n8", "memorize('10')"),
            ("n20", "memorize('10')"),
            ("n30", "click('bad')"),
        ]
        current = PlanState(
            Plan(
                {
                    "nodes": [
                        {"node_id": node, "action": action, "description": node}
                        for node, action in pairs
                    ],
                    "edges": [],
                }
            ),
            "",
        )
        agent.execute.return_value = "<plan components>:\n" + "\n".join(
            f"'''{node}: {action}'''" for node, action in pairs
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory, 1, "map")
            task = SimpleNamespace(
                query="Remember both pages", steps=0, plan=Plan.init_empty(), long_term=[]
            )
            subgoal = SubgoalState("Remember pages", "", True)
            pointer = RollBackPointer(browser.url)
            batch = ExecutionService(browser, agent, memory, {}, store).execute(
                task, subgoal, current
            )
            self.assertEqual(len(memory.to_dict()), 3)
            self.assertTrue(all(isinstance(change, MemoryWrite) for change in batch.memory_changes))
            self.assertIn("http://example.test/page2", memory.get_memory_by_bid("10"))
            from skilltracer.agents.summarizer import Assessment

            assessment = Assessment(
                False,
                "c2",
                error_node="n8",
                repair_signal={
                    "tool": "REPLACE_ACTION",
                    "arguments": {"node_id": "n8", "new_action": "memorize(result_id)"},
                },
            )
            RepairService(browser, memory, pointer, store).apply(
                task, subgoal, current, batch, assessment
            )
            self.assertEqual(len(memory.to_dict()), 1)
            self.assertEqual(memory.latest_record_id("10"), batch.memory_changes[0].memory_id)
            self.assertIn("Target", memory.to_prompt())
            self.assertNotIn("Page two", memory.to_prompt())
            self.assertEqual(subgoal.actions, ["memorize('10')", "click('10')"])


class InfeasibilityResponseTests(unittest.TestCase):
    def test_infeasibility_reason_is_a_final_response(self):
        reason = "No editable shipping field: \"quotes\", C:\\new\nLiteral ''' fences 🧭"
        self.assertEqual(extract_response([f"report_infeasible({reason!r})"]), reason)

    def test_both_keyword_and_positional_messages_keep_their_order(self):
        self.assertEqual(
            extract_response(
                [
                    "send_msg_to_user(text='First')",
                    "report_infeasible(reason='Cannot continue')",
                    "send_msg_to_user('Last')",
                ]
            ),
            "First\nCannot continue\nLast",
        )

    def test_invalid_or_nonliteral_messages_are_ignored_without_execution(self):
        self.assertEqual(
            extract_response(
                [
                    "report_infeasible(__import__('os').getcwd())",
                    "report_infeasible(other='wrong field')",
                    "report_infeasible(123)",
                    "report_infeasible('')",
                    "report_infeasible('one', 'two')",
                    "broken(",
                ]
            ),
            "",
        )

    def test_reason_reaches_the_workflow_evaluator_through_the_existing_interface(self):
        reason = "The placed order has no editable delivery address."
        models = {
            role: Mock(side_effect=AssertionError("Unexpected model call"))
            for role in (
                "subgoal_generator",
                "planner",
                "executor",
                "summarizer",
                "skill_reuser",
                "skill_inducer",
            )
        }
        models.update(
            subgoal_generator=QueueModel(
                ["subgoal: Explain the unavailable action\nterminate: true\nevidence: Observed"]
            ),
            planner=QueueModel([plan_text(["report_infeasible(reason)"])]),
            executor=QueueModel(
                [
                    f"<plan components>:\n'''n1: report_infeasible({reason!r})'''\n<context>: Observed"
                ]
            ),
            summarizer=QueueModel([SUCCESS_FLAGS]),
        )
        evaluator = FakeEvaluator()
        with tempfile.TemporaryDirectory() as directory:
            workflow = Workflow(
                models,
                1,
                "map",
                "http://example.test",
                browser=FakeBrowser(),
                evaluator=evaluator,
                state_dir=Path(directory) / "state",
                results_dir=Path(directory) / "results",
            )
            result = workflow.run("Change the delivery address", maximum_steps=3)
            self.assertEqual(evaluator.info["response"], reason)
            self.assertEqual(extract_response(result["executed_actions"]), reason)
