"""Login field and action-grounding boundary regressions."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from skilltracer.browser.login import login_site_with_env
from skilltracer.memory.active import ActiveMemory
from skilltracer.planning.plan import Plan
from skilltracer.runtime.execution import ExecutionService
from skilltracer.runtime.results import ArtifactStore
from skilltracer.runtime.state import PlanState


class BrowserBoundaryTests(unittest.TestCase):
    def test_login_accepts_mixed_attribute_representations(self):
        page = SimpleNamespace(url="http://localhost:7770/customer/account/")
        page.wait_for_url = Mock(
            side_effect=lambda predicate, **kwargs: self.assertTrue(predicate(page.url))
        )
        browser = Mock()
        browser.env = SimpleNamespace(unwrapped=SimpleNamespace(page=page))
        browser.execute_browsergym_action.return_value = (True, False, False)
        browser.get_page_state.return_value = {"axtree_object": "fixture"}
        elements = [
            {"id": "10", "role": "textbox", "value": "Email", "attributes": ["required"]},
            {
                "id": "11",
                "role": "textbox",
                "value": "Password",
                "attributes": ["required", {"name": "type", "value": "password"}],
            },
            {"id": "12", "role": "button", "value": "Sign In", "attributes": []},
        ]
        with patch("skilltracer.browser.login.extract_all_elements", return_value=elements):
            self.assertTrue(login_site_with_env(browser, "shopping"))
        self.assertEqual(browser.execute_browsergym_action.call_count, 4)
        browser.refresh.assert_called_once()

    def test_null_memory_action_preserves_later_node_association(self):
        response = (
            "<plan components>:\n'''n1: memorize('Null')'''\n"
            "'''n2: memorize('20')'''\n<context>:\n[20] heading 'Evidence'"
        )
        browser = Mock()
        browser.get_page_state.return_value = {
            "url": "http://example.test",
            "axtree_object": "[20] heading 'Evidence'",
        }
        agent = Mock()
        agent.execute.return_value = response
        service = ExecutionService(
            browser, agent, ActiveMemory(), {}, ArtifactStore(".", 261, "map")
        )
        plan = Plan(
            {
                "nodes": [
                    {"node_id": "n1", "action": "memorize(none)", "description": ""},
                    {
                        "node_id": "n2",
                        "action": "memorize(evidence)",
                        "description": "Keep evidence",
                    },
                ],
                "edges": [],
                "next_step_decision": {"should_plan_next": False},
            }
        )
        task = SimpleNamespace(query="Keep evidence", steps=0)
        batch = service.execute(
            task, SimpleNamespace(goal="Remember evidence"), PlanState(plan, "")
        )
        self.assertEqual(batch.memory_pairs, [("n2", "20")])
        browser.execute_browsergym_action.assert_not_called()

    def test_unknown_node_is_rejected_before_browser_interaction(self):
        browser = Mock()
        browser.get_page_state.return_value = {"url": "http://example.test", "axtree_object": ""}
        agent = Mock()
        agent.execute.return_value = "<plan components>:\n'''n99: click('20')'''\n<context>:"
        service = ExecutionService(
            browser, agent, ActiveMemory(), {}, ArtifactStore(".", 261, "map")
        )
        plan = Plan(
            {
                "nodes": [{"node_id": "n1", "action": "click(target)"}],
                "edges": [],
                "next_step_decision": {"should_plan_next": False},
            }
        )
        with self.assertRaisesRegex(ValueError, "unknown plan node"):
            service.execute(
                SimpleNamespace(query="Open target", steps=0),
                SimpleNamespace(goal="Open target"),
                PlanState(plan, ""),
            )
        browser.execute_browsergym_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
