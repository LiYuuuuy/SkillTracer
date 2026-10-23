"""Verify enforced skill graphs, exact content and execution of only the new suffix."""

import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from test_workflow import SUCCESS_FLAGS, FakeBrowser, FakeEvaluator, QueueModel, skill_text

from skilltracer.agents.planner import Planner
from skilltracer.llm import ROLES
from skilltracer.planning.plan import Plan
from skilltracer.planning.reuse import manage_skill_plan
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.workflow import Workflow


def example_skill():
    return {
        "id": "s1",
        "name": "Find result",
        "description": "Find a result",
        "plan": {
            "nodes": [
                {
                    "node_id": "n1",
                    "purpose": "navigation",
                    "component_type": "link",
                    "action": "click(category_id)",
                    "description": "Open the category.",
                },
                {
                    "node_id": "n2",
                    "purpose": "search",
                    "component_type": "textbox",
                    "action": "fill(search_id, query_text)",
                    "description": "Enter the query.",
                },
                {
                    "node_id": "n3",
                    "purpose": "commit_input",
                    "component_type": "textbox",
                    "action": "press(search_id, 'Enter')",
                    "description": "Submit the query.",
                },
            ],
            "edges": [
                {"from": "n1", "to": "n2", "preconditions": ["exists_content('Search')"]},
                {
                    "from": "n2",
                    "to": "n3",
                    "precondition": "exists_component(component_type='textbox')",
                },
            ],
            "next_step_decision": {"should_plan_next": False},
        },
    }


def subset(skill, indices, start=10):
    graph = skill["plan"]
    ids = {
        graph["nodes"][index]["node_id"]: f"n{start + offset}"
        for offset, index in enumerate(indices)
    }
    return {
        "use_skill": True,
        "nodes": [
            {
                **copy.deepcopy(graph["nodes"][index]),
                "node_id": ids[graph["nodes"][index]["node_id"]],
            }
            for index in indices
        ],
        "edges": [
            {**copy.deepcopy(edge), "from": ids[edge["from"]], "to": ids[edge["to"]]}
            for edge in graph["edges"]
            if edge["from"] in ids and edge["to"] in ids
        ],
        "next_step_decision": {"should_plan_next": False},
    }


class SkillReuseTests(unittest.TestCase):
    def setUp(self):
        self.skill = example_skill()
        self.empty = Plan.init_empty()

    def manage(self, graph, previous=None):
        return manage_skill_plan(json.dumps(graph), previous or self.empty, json.dumps(self.skill))

    def test_full_partial_and_disconnected_subgraphs_preserve_planner_output(self):
        for indices in ([0, 1, 2], [0, 1], [1, 2], [0, 2], [1]):
            with self.subTest(indices=indices):
                graph = subset(self.skill, indices)
                decision = self.manage(graph)
                self.assertFalse(decision.replaced)
                self.assertEqual(decision.reason, "skill_subgraph")
                self.assertEqual(decision.plan.to_dict(), graph)
                self.assertEqual(
                    list(decision.node_mapping.values()), [f"n{i + 1}" for i in indices]
                )

    def test_changed_node_content_or_condition_replaces_the_whole_graph(self):
        variants = []
        for key in ("purpose", "component_type", "action", "description"):
            graph = subset(self.skill, [0, 1, 2])
            graph["nodes"][1][key] += " changed"
            variants.append(graph)
        graph = subset(self.skill, [0, 1, 2])
        graph["edges"][0]["preconditions"] = ["exists_content('Invented')"]
        variants.append(graph)
        graph = subset(self.skill, [0, 1, 2])
        graph["edges"] = []
        variants.append(graph)
        graph = subset(self.skill, [0, 1, 2])
        graph["nodes"].reverse()
        variants.append(graph)
        graph = subset(self.skill, [0, 1, 2])
        graph["nodes"][0]["invented"] = True
        variants.append(graph)
        for graph in variants:
            with self.subTest(graph=graph):
                original = copy.deepcopy(graph)
                decision = self.manage(graph)
                self.assertTrue(decision.replaced)
                self.assertTrue(decision.plan.get("use_skill"))
                self.assertEqual(decision.plan.get("nodes"), self.skill["plan"]["nodes"])
                self.assertEqual(decision.plan.get("edges"), self.skill["plan"]["edges"])
                self.assertEqual(graph, original)

    def test_invalid_or_empty_declared_graph_falls_back_to_valid_skill(self):
        variants = [
            {"use_skill": True, "nodes": [], "edges": []},
            {"use_skill": True},
            {"use_skill": True, "nodes": None},
            {"use_skill": True, "nodes": [None]},
            {"use_skill": True, "nodes": [{"node_id": "n1"}]},
        ]
        duplicate = subset(self.skill, [0, 1])
        duplicate["nodes"][1]["node_id"] = duplicate["nodes"][0]["node_id"]
        variants.append(duplicate)
        dangling = subset(self.skill, [0])
        dangling["edges"] = [{"from": "n10", "to": "missing"}]
        variants.append(dangling)
        for graph in variants:
            with self.subTest(graph=graph):
                self.assertTrue(self.manage(graph).replaced)

    def test_append_reserves_history_ids_and_does_not_return_the_executed_prefix(self):
        previous = Plan(
            {
                "nodes": [{"node_id": "n20", "action": "hover(target_id)", "description": "Done"}],
                "edges": [],
            }
        )
        before = copy.deepcopy(self.skill)
        decision = self.manage({"use_skill": True, "nodes": []}, previous)
        self.assertEqual(
            [node["node_id"] for node in decision.plan.get("nodes")], ["n21", "n22", "n23"]
        )
        self.assertEqual(decision.node_mapping, {"n21": "n1", "n22": "n2", "n23": "n3"})
        self.assertEqual(
            decision.plan.get("edges")[0],
            {"from": "n21", "to": "n22", "preconditions": ["exists_content('Search')"]},
        )
        self.assertEqual(self.skill, before)
        self.assertEqual(previous.get("nodes")[0]["node_id"], "n20")

    def test_valid_entry_preserves_exact_partial_plan_and_original_ids(self):
        for indices in ([0, 1], [1, 2], [2]):
            with self.subTest(indices=indices):
                graph = subset(self.skill, indices, start=20)
                entry = self.skill["plan"]["nodes"][indices[0]]["node_id"]
                graph["skill_start_node_id"] = entry
                decision = self.manage(graph)
                self.assertFalse(decision.replaced)
                self.assertEqual(decision.plan.to_dict(), graph)
                self.assertEqual(decision.skill_start_node_id, entry)

    def test_mismatch_appends_suffix_including_entry_and_preserves_completion(self):
        previous = Plan({"nodes": [{"node_id": "n20", "action": "hover(target_id)"}], "edges": []})
        skill_before, previous_before = copy.deepcopy(self.skill), previous.to_dict()
        for entry, indices in (("n1", [0, 1, 2]), ("n2", [1, 2]), ("n3", [2])):
            with self.subTest(entry=entry):
                graph = subset(self.skill, indices)
                graph["skill_start_node_id"] = entry
                graph["nodes"][0]["description"] += " slightly changed"
                graph["next_step_decision"]["should_plan_next"] = True
                decision = self.manage(graph, previous)
                self.assertTrue(decision.replaced)
                self.assertEqual(decision.skill_start_node_id, entry)
                self.assertEqual(
                    list(decision.node_mapping.values()), [f"n{i + 1}" for i in indices]
                )
                self.assertEqual(
                    [node["node_id"] for node in decision.plan.get("nodes")],
                    [f"n{21 + i}" for i in range(len(indices))],
                )
                for node, index in zip(decision.plan.get("nodes"), indices):
                    self.assertEqual(
                        {**node, "node_id": f"n{index + 1}"},
                        self.skill["plan"]["nodes"][index],
                    )
                expected_edges = subset(self.skill, indices, start=21)["edges"]
                self.assertEqual(decision.plan.get("edges"), expected_edges)
                self.assertEqual(decision.plan.get("skill_start_node_id"), entry)
                self.assertFalse(decision.plan.get("next_step_decision")["should_plan_next"])
        self.assertEqual(self.skill, skill_before)
        self.assertEqual(previous.to_dict(), previous_before)

    def test_invalid_entry_cannot_skip_skill_nodes(self):
        for entry in (None, "missing", "n20", 2, True, [], {}):
            with self.subTest(entry=entry):
                graph = subset(self.skill, [1, 2], start=20)
                graph["skill_start_node_id"] = entry
                decision = self.manage(graph)
                self.assertTrue(decision.replaced)
                self.assertIn("skill_start_node_id", decision.reason)
                self.assertEqual(decision.skill_start_node_id, "n1")
                self.assertEqual(decision.plan.get("nodes"), self.skill["plan"]["nodes"])

    def test_entry_must_be_first_new_node_even_when_graph_is_a_skill_subgraph(self):
        for entry, indices in (("n2", [0, 1, 2]), ("n2", [2]), ("n1", [1, 2])):
            with self.subTest(entry=entry, indices=indices):
                graph = subset(self.skill, indices)
                graph["skill_start_node_id"] = entry
                decision = self.manage(graph)
                self.assertTrue(decision.replaced)
                self.assertEqual(next(iter(decision.node_mapping.values())), entry)

    def test_entry_anchors_identical_node_content(self):
        self.skill["plan"]["nodes"][1] = {**self.skill["plan"]["nodes"][0], "node_id": "n2"}
        graph = subset(self.skill, [1])
        graph["skill_start_node_id"] = "n2"
        decision = self.manage(graph)
        self.assertFalse(decision.replaced)
        self.assertEqual(decision.node_mapping, {"n10": "n2"})

    def test_empty_or_invalid_graph_still_uses_valid_entry(self):
        for graph in (
            {"nodes": [], "edges": []},
            {},
            {"nodes": [None]},
            {
                "nodes": [{"node_id": "n20", "action": "click(target_id)"}],
                "edges": [{"from": "n20", "to": "missing"}],
            },
        ):
            with self.subTest(graph=graph):
                decision = self.manage({**graph, "use_skill": True, "skill_start_node_id": "n2"})
                self.assertTrue(decision.replaced)
                self.assertEqual(list(decision.node_mapping.values()), ["n2", "n3"])

    def test_cumulative_prefix_is_preserved_when_replacing_from_entry(self):
        previous = Plan({"nodes": [{"node_id": "n1", "action": "hover(target_id)"}], "edges": []})
        graph = subset(self.skill, [1, 2], start=2)
        graph["skill_start_node_id"] = "n2"
        graph["nodes"] = previous.to_dict()["nodes"] + graph["nodes"]
        graph["edges"].append({"from": "n1", "to": "n2"})
        self.assertFalse(self.manage(graph, previous).replaced)
        graph["nodes"][0]["action"] = "click(changed_id)"
        decision = self.manage(graph, previous)
        self.assertTrue(decision.replaced)
        self.assertEqual(list(decision.node_mapping.values()), ["n2", "n3"])
        self.assertEqual(previous.get("nodes")[0]["action"], "hover(target_id)")

    def test_cumulative_plan_keeps_prefix_and_matches_only_new_graph(self):
        previous = Plan(
            {
                "nodes": [{"node_id": "n1", "action": "hover(target_id)", "description": "Done"}],
                "edges": [],
            }
        )
        graph = subset(self.skill, [1, 2], start=2)
        graph["nodes"] = previous.to_dict()["nodes"] + graph["nodes"]
        graph["edges"].append({"from": "n1", "to": "n2"})
        decision = self.manage(graph, previous)
        self.assertFalse(decision.replaced)
        self.assertEqual([node["node_id"] for node in decision.plan.get("nodes")], ["n2", "n3"])
        graph["nodes"][0]["description"] = "Changed committed content"
        self.assertTrue(self.manage(graph, previous).replaced)
        graph["nodes"][0]["description"] = "Done"
        graph["edges"][-1]["precondition"] = "exists_content('Invented')"
        self.assertTrue(self.manage(graph, previous).replaced)

    def test_identical_nodes_use_edge_constraints_to_resolve_mapping(self):
        self.skill["plan"]["nodes"][1] = {**self.skill["plan"]["nodes"][0], "node_id": "n2"}
        graph = subset(self.skill, [1, 2])
        decision = self.manage(graph)
        self.assertFalse(decision.replaced)
        self.assertEqual(decision.node_mapping, {"n10": "n2", "n11": "n3"})

    def test_false_or_legacy_missing_flag_skips_reuse_validation(self):
        graph = subset(self.skill, [0])
        graph["nodes"][0]["description"] = "Free planning allowed"
        for flag in (False, None):
            if flag is None:
                graph.pop("use_skill", None)
            else:
                graph["use_skill"] = flag
            graph["skill_start_node_id"] = "invalid-but-unused"
            decision = manage_skill_plan(json.dumps(graph), self.empty, "None")
            self.assertFalse(decision.requested)
            self.assertEqual(decision.plan.to_dict(), graph)
        graph["use_skill"] = "true"
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.manage(graph)
        graph["use_skill"] = True
        with self.assertRaisesRegex(ValueError, "without an injected skill"):
            manage_skill_plan(json.dumps(graph), self.empty, "None")

    def test_json_plan_with_quotes_braces_and_code_fences_roundtrips(self):
        self.skill["plan"]["nodes"][0]["description"] = (
            'Keep "quotes", {braces}, \\ paths, ```plan\ncode\n``` and apostrophes.'
        )
        graph = subset(self.skill, [0])
        for text in (
            Plan(graph).to_symbolic(),
            "```plan " + json.dumps(graph) + "```",
            "'''plan" + json.dumps(graph) + "'''",
        ):
            self.assertEqual(Plan.from_symbolic(text).to_dict(), graph)
            self.assertFalse(manage_skill_plan(text, self.empty, json.dumps(self.skill)).replaced)

    def test_workflow_checks_later_plans_and_executes_only_appended_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repositories = Repositories.open(root / "state", "map")
            repositories.skills.write_back([{**self.skill, "id": f"s{i}"} for i in (1, 2, 3)])
            first = {
                "use_skill": False,
                "nodes": [
                    {
                        "node_id": "n1",
                        "purpose": "navigation",
                        "component_type": "link",
                        "action": "hover(target_id)",
                        "description": "Observe target",
                    }
                ],
                "edges": [],
                "next_step_decision": {"should_plan_next": True},
            }
            broken = subset(self.skill, [0], start=2)
            broken["nodes"][0]["action"] = "click(changed_id)"
            responses = {
                "subgoal_generator": [
                    "subgoal: Find result\nterminate: true\nevidence: [10] link 'Target'"
                ],
                "planner": [Plan(first).to_symbolic(), Plan(broken).to_symbolic()],
                "executor": [
                    "<plan components>:\n'''n1: hover('10')'''\n<context>: [10] link 'Target'",
                    "<plan components>:\n'''n2: click('10')'''\n<context>: [10] link 'Target'",
                    "<plan components>:\n'''n3: fill('20', 'query')'''\n<context>: [20] textbox 'Search'",
                    "<plan components>:\n'''n4: press('20', 'Enter')'''\n<context>: [20] textbox 'Search'",
                ],
                "summarizer": [SUCCESS_FLAGS] * 4,
                "skill_reuser": ["match: true\nid: s1"],
                "skill_inducer": [skill_text()],
            }
            models = {role: QueueModel(responses[role]) for role in ROLES}
            browser = FakeBrowser()
            workflow = Workflow(
                models,
                261,
                "map",
                "http://example.test",
                browser=browser,
                evaluator=FakeEvaluator(),
                results_dir=root / "results",
                state_dir=root / "state",
            )
            outcome = workflow.run("Find result", maximum_steps=6)
            self.assertEqual(outcome["evaluation"]["status"], "success")
            self.assertEqual(browser.actions.count("hover('10')"), 1)
            self.assertNotIn("changed_id", str(browser.actions))
            self.assertIn(
                {"from": "n2", "to": "n3", "preconditions": ["exists_content('Search')"]},
                workflow.assets.task.plan.get("edges"),
            )
            self.assertFalse(
                workflow.assets.task.plan.get("next_step_decision")["should_plan_next"]
            )
            self.assertEqual(
                [node["node_id"] for node in workflow.assets.task.plan.get("nodes")],
                ["n1", "n2", "n3", "n4"],
            )
            self.assertIn('"id": "s1"', models["planner"].prompts[1])
            events = [
                json.loads(line)
                for line in (Path(outcome["assets_dir"]) / "events.jsonl").read_text().splitlines()
            ]
            decisions = [item["data"] for item in events if item["event"] == "skill_reuse_checked"]
            self.assertEqual(
                [(item["requested"], item["replaced"]) for item in decisions],
                [(False, False), (True, True)],
            )
            self.assertTrue(browser.closed)

    def test_workflow_resumes_before_entry_and_commits_remaining_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repositories = Repositories.open(root / "state", "map")
            repositories.skills.write_back([{**self.skill, "id": f"s{i}"} for i in (1, 2, 3)])
            first = subset(self.skill, [0], start=1)
            first["use_skill"] = False
            first["next_step_decision"]["should_plan_next"] = True
            broken = subset(self.skill, [1, 2], start=2)
            broken["skill_start_node_id"] = "n2"
            broken["nodes"][0]["description"] += " slightly changed"
            responses = {
                "subgoal_generator": ["subgoal: Find result\nterminate: true\nevidence: Target"],
                "planner": [Plan(first).to_symbolic(), Plan(broken).to_symbolic()],
                "executor": [
                    "<plan components>:\n'''n1: click('10')'''\n<context>: Target",
                    "<plan components>:\n'''n2: fill('20', 'query')'''\n<context>: Search",
                    "<plan components>:\n'''n3: press('20', 'Enter')'''\n<context>: Search",
                ],
                "summarizer": [SUCCESS_FLAGS] * 3,
                "skill_reuser": ["match: true\nid: s1"],
                "skill_inducer": [skill_text()],
            }
            models = {role: QueueModel(responses[role]) for role in ROLES}
            browser = FakeBrowser()
            workflow = Workflow(
                models,
                261,
                "map",
                "http://example.test",
                browser=browser,
                evaluator=FakeEvaluator(),
                results_dir=root / "results",
                state_dir=root / "state",
            )
            outcome = workflow.run("Find result", maximum_steps=5)
            self.assertEqual(outcome["evaluation"]["status"], "success")
            self.assertEqual(browser.actions.count("click('10')"), 1)
            self.assertEqual(len(models["executor"].prompts), 3)
            self.assertEqual(
                [node["action"] for node in workflow.assets.task.plan.get("nodes")],
                [node["action"] for node in self.skill["plan"]["nodes"]],
            )
            self.assertIn(self.skill["plan"]["edges"][1], workflow.assets.task.plan.get("edges"))
            self.assertFalse(
                workflow.assets.task.plan.get("next_step_decision")["should_plan_next"]
            )
            events = [
                json.loads(line)
                for line in (Path(outcome["assets_dir"]) / "events.jsonl").read_text().splitlines()
            ]
            decision = [
                event["data"] for event in events if event["event"] == "skill_reuse_checked"
            ][-1]
            self.assertTrue(decision["replaced"])
            self.assertEqual(decision["skill_start_node_id"], "n2")
            self.assertEqual(decision["node_mapping"], {"n2": "n2", "n3": "n3"})
            self.assertTrue(browser.closed)

    def test_no_match_and_empty_bank_remove_skill_context_from_actual_model_calls(self):
        for initial_skills in (False, True):
            with (
                self.subTest(initial_skills=initial_skills),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                repositories = Repositories.open(root / "state", "map")
                if initial_skills:
                    repositories.skills.write_back(
                        [{**self.skill, "id": f"s{i}"} for i in (1, 2, 3)]
                    )
                graph = subset(self.skill, [0], start=1)
                graph.pop("use_skill")
                responses = {
                    "subgoal_generator": [
                        "subgoal: Find result\nterminate: true\nevidence: Target"
                    ],
                    "planner": [Plan(graph).to_symbolic()],
                    "executor": ["<plan components>:\n'''n1: click('10')'''\n<context>: Target"],
                    "summarizer": [SUCCESS_FLAGS],
                    "skill_reuser": ["match: false\nid: s1"] if initial_skills else [],
                    "skill_inducer": [],
                }
                models = {role: QueueModel(responses[role]) for role in ROLES}
                workflow = Workflow(
                    models,
                    261,
                    "map",
                    "http://example.test",
                    browser=FakeBrowser(),
                    evaluator=FakeEvaluator(),
                    results_dir=root / "results",
                    state_dir=root / "state",
                )
                outcome = workflow.run("Find result", maximum_steps=3)
                self.assertEqual(outcome["evaluation"]["status"], "success")
                self.assertNotIn("skill", models["planner"].prompts[0].lower())
                self.assertEqual(len(models["skill_reuser"].prompts), int(initial_skills))

    def test_planner_omits_all_skill_instructions_fields_and_labels_without_match(self):
        for missing in (None, "None", "null", "", "  NONE  ", "\nNULL\n"):
            with self.subTest(missing=missing):
                model = QueueModel(["ordinary plan"])
                planner = Planner(model, "registry", "actions")
                self.assertEqual(
                    planner.plan(query="q", subgoal="g", axtree="page", skill=missing),
                    "ordinary plan",
                )
                self.assertNotIn("skill", model.prompts[0].lower())

    def test_matched_prompt_explains_inclusive_original_entry_without_substituting_data(self):
        self.skill["description"] = "literal $reuse_fields and $subgoal"
        model = QueueModel(["plan"])
        planner = Planner(model, "registry", "actions")
        planner.plan(query="q", subgoal="changed", axtree="page", skill=json.dumps(self.skill))
        prompt = model.prompts[0]
        self.assertIn("skill_start_node_id", prompt)
        self.assertIn("BEFORE", prompt)
        self.assertIn("has NOT executed yet", prompt)
        self.assertIn("INCLUDING", prompt)
        self.assertIn("literal $reuse_fields and $subgoal", prompt)

    def test_planner_public_call_still_returns_text(self):
        response = Plan(subset(self.skill, [0])).to_symbolic()
        planner = Planner(lambda _: response, "", "")
        self.assertIsInstance(
            planner.plan(query="q", subgoal="g", axtree="", skill=json.dumps(self.skill)), str
        )
        self.assertEqual(
            list(inspect.signature(Planner.plan).parameters),
            [
                "self",
                "query",
                "subgoal",
                "axtree",
                "skill",
                "past_plan",
                "plan_status",
                "executed_plan",
                "history_plan",
                "history_actions",
                "active_memory",
                "task_description",
            ],
        )
