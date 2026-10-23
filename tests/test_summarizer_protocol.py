"""Keep two-stage assessment and literal repair fields robust to observed text."""

import inspect
import json
import unittest
from unittest.mock import Mock, patch

from skilltracer.agents.repair_parsing import parse_repair
from skilltracer.agents.summarizer import FLAG_NAMES, Assessment, InvalidAssessment, Summarizer
from skilltracer.planning.checkpoints import CheckPointManager

PASS = "true\ntrue\nfalse\nfalse\nfalse\ntrue"
FAIL = "false\nfalse\nfalse\nfalse\nfalse\ntrue"


def field(name, value):
    return name + ": |\n" + "\n".join("  " + line for line in value.split("\n"))


def repair_text(evidence, *, parameter="new_description", value="Use the observed target."):
    tool = {
        "new_description": "REFINE_DESCRIPTION",
        "new_action": "REPLACE_ACTION",
        "new_purpose": "REPLACE_PURPOSE",
        "new_component_type": "REPLACE_COMPONENT_TYPE",
    }[parameter]
    return "\n".join(
        [
            "error_node: n7",
            "repair_tool: " + tool,
            "node_id: n7",
            field(parameter, value),
            field("error_type", "Grounding mismatch"),
            field("evidence", evidence),
            field("summary", "Target unavailable"),
            field("insight", evidence),
            field("subgoal_guidance", "Clarify the target"),
        ]
    )


def summarize(agent, **overrides):
    context = dict(
        query="Find target",
        subgoal="Open target",
        plan="plan",
        delta_axtree_traj="Observed",
        executed_actions="actions",
        executed_actions_list=[],
        executed_nodes_list="",
        active_memory="",
        short_term_memory="",
        long_term_memory="",
        checkpoints=CheckPointManager({"nodes": [{"node_id": "n7"}]}),
        last_checkpoint="c1",
    )
    return agent.summarize(**{**context, **overrides})


class SummarizerProtocolTests(unittest.TestCase):
    def test_first_stage_success_skips_second_stage(self):
        model = Mock(return_value=PASS)
        agent = Summarizer(model)
        calls = []
        agent.recorder = lambda role, template, prompt, **kwargs: calls.append(template)
        result = summarize(agent)
        self.assertIsInstance(result, Assessment)
        self.assertTrue(result.plan_success)
        self.assertEqual(calls, ["judge"])
        self.assertEqual(model.call_count, 1)

    def test_second_stage_runs_only_when_required_and_returns_the_same_shape(self):
        model = Mock(side_effect=[FAIL, repair_text('Observed "quote" and C:\\Users\\name')])
        agent = Summarizer(model)
        calls = []
        agent.recorder = lambda role, template, prompt, **kwargs: calls.append(template)
        result = summarize(agent)
        self.assertEqual(calls, ["judge", "repair"])
        self.assertEqual(model.call_count, 2)
        self.assertFalse(result.plan_success)
        self.assertEqual(result.checkpoint, "c0")
        self.assertEqual(
            set(result.to_dict()),
            {
                "plan_success",
                "checkpoint",
                "error_node",
                "error_type",
                "error_evidence",
                "repair_signal",
                "insight",
                "guidance",
                "flags",
            },
        )
        self.assertEqual(json.loads(json.dumps(result.to_dict())), result.to_dict())

    def test_literal_fields_never_parse_observed_text_as_json(self):
        evidence = '[10] link \'Bob\'s "offer"\' {broken: "json} C:\\Users\\new\\test\nrepair_tool: REPLACE_PURPOSE\nerror_node: n999\n{"repair_signal":{"tool":"REPLACE_ACTION"}}\n```json\n{unbalanced\n```\ntriple quotes: \'\'\' and backtick `'
        with patch(
            "skilltracer.agents.repair_parsing.json.loads",
            side_effect=AssertionError("No JSON parsing for literal fields"),
        ):
            result, guidance = parse_repair(repair_text(evidence))
        self.assertEqual(result["error_node"], "n7")
        self.assertEqual(result["repair_signal"]["tool"], "REFINE_DESCRIPTION")
        self.assertNotIn("evidence", result)
        self.assertNotIn("new_description", result)
        self.assertEqual(result["error_evidence"]["evidence"], evidence)
        self.assertEqual(result["insight"], evidence)
        self.assertEqual(guidance, "Clarify the target")
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_quotes_backslashes_unicode_and_control_characters_survive(self):
        samples = [
            "\"double\" and 'single' {braces} [arrays]: colon",
            r"C:\Users\new\folder\tab\name \q \uNOTHEX",
            "nul:\x00 tab:\t vertical:\v form:\f sep:\u2028 paragraph:\u2029",
            "emoji: 🧭 café e\u0301 dash — arrows →",
            "first\n\n    nested indentation\nlast",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                data, _ = parse_repair(repair_text(sample, value=sample))
                self.assertEqual(data["error_evidence"]["evidence"], sample)
                self.assertEqual(data["repair_signal"]["arguments"]["new_description"], sample)
                self.assertEqual(json.loads(json.dumps(data)), data)

    def test_all_four_tools_and_multiline_arguments_are_assembled_by_program(self):
        for parameter in ("new_purpose", "new_component_type", "new_action", "new_description"):
            with self.subTest(parameter=parameter):
                action = 'send_msg_to_user("Bob\'s \\"offer\\" at C:\\\\new")'
                data, _ = parse_repair(repair_text("Evidence", parameter=parameter, value=action))
                self.assertEqual(
                    data["repair_signal"]["arguments"], {"node_id": "n7", parameter: action}
                )

    def test_legacy_json_and_fenced_literal_fields_remain_accepted(self):
        payload = {
            "error_node": "n7",
            "repair_signal": {
                "tool": "REPLACE_ACTION",
                "arguments": {"node_id": "n7", "new_action": "click(alternative_id)"},
            },
            "error_evidence": {"evidence": '"quotes" \\ { }', "summary": "Target"},
            "subgoal_guidance": None,
        }
        for text in (json.dumps(payload), "```json\n" + json.dumps(payload) + "\n```"):
            parsed, guidance = parse_repair(text)
            self.assertEqual(parsed["repair_signal"], payload["repair_signal"])
            self.assertEqual(guidance, "Null")
        data, _ = parse_repair(
            "'''summary\n" + repair_text("Literal ``` and ''' symbols") + "\n'''"
        )
        self.assertEqual(data["error_evidence"]["evidence"], "Literal ``` and ''' symbols")

    def test_duplicate_control_fields_and_malformed_json_fail_cleanly(self):
        with self.assertRaisesRegex(ValueError, "Duplicate repair field"):
            parse_repair(repair_text("Observed") + "\nerror_node: n9")
        model = Mock(side_effect=[FAIL, '{"error_node":"n7","insight":"bad "quotes""}'])
        with self.assertRaises(InvalidAssessment):
            summarize(Summarizer(model))

    def test_judge_parses_boolean_lines_without_counting_observed_boolean_words(self):
        response = 'Observation: "true false" {raw} C:\\Users\\name\n' + PASS
        self.assertTrue(summarize(Summarizer(lambda _: response)).plan_success)
        values = [True, True, False, False, False, True]
        named = "\n".join(
            f"{name}: {str(value).lower()}"
            for name, value in reversed(list(zip(FLAG_NAMES, values)))
        )
        self.assertEqual(
            summarize(Summarizer(lambda _: named)).flags, dict(zip(FLAG_NAMES, values))
        )
        numbered = "\n".join(
            f"{index}. {str(value).lower()}"
            for index, value in reversed(list(enumerate(values, 1)))
        )
        self.assertEqual(
            summarize(Summarizer(lambda _: numbered)).flags, dict(zip(FLAG_NAMES, values))
        )
        with self.assertRaises(InvalidAssessment):
            summarize(Summarizer(lambda _: "1. true\n1. false\n" + PASS))
        model = Mock(side_effect=[PASS, repair_text("Browser reported an error")])
        self.assertFalse(summarize(Summarizer(model), action_errors=["Actual error"]).plan_success)
        self.assertEqual(model.call_count, 2)

    def test_summarizer_public_signature_is_unchanged(self):
        self.assertEqual(
            list(inspect.signature(Summarizer.summarize).parameters),
            [
                "self",
                "query",
                "subgoal",
                "plan",
                "delta_axtree_traj",
                "executed_actions",
                "executed_actions_list",
                "executed_nodes_list",
                "active_memory",
                "short_term_memory",
                "long_term_memory",
                "checkpoints",
                "last_checkpoint",
                "action_errors",
            ],
        )
