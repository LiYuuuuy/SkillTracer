"""Task, provider, protocol and release-boundary regressions."""

import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from scripts.package_release import build_release
from skilltracer.agents.base import Agent
from skilltracer.agents.executor import Executor
from skilltracer.agents.parsing import split_plan_and_evidence
from skilltracer.agents.planner import Planner
from skilltracer.agents.repair_parsing import parse_repair
from skilltracer.agents.subgoal import SubgoalGenerator
from skilltracer.cli import build_parser, main, select_tasks
from skilltracer.config import completion_options
from skilltracer.llm import ROLES, build_multi_llms
from skilltracer.planning.plan import Plan, slice_filled_plan_before_node
from skilltracer.runtime.repositories import Repositories
from skilltracer.tasks import TaskRepository, resolve_start_url

ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def test_task_origin_override_preserves_deep_link(self):
        with patch.dict(
            "skilltracer.tasks.SITE_URLS",
            {
                "shopping_admin": "http://127.0.0.1:18780/admin",
            },
        ):
            self.assertEqual(
                resolve_start_url("None:7780/admin/catalog?id=1481#price"),
                "http://127.0.0.1:18780/admin/catalog?id=1481#price",
            )

    def test_all_bundled_tasks_are_loadable(self):
        repository = TaskRepository()
        self.assertEqual(len(repository.ids()), 812)
        for task_id in repository.ids():
            task = repository.load(task_id)
            self.assertTrue(task.intent)
            self.assertTrue(task.sites)
            self.assertTrue(all(url.startswith(("http://", "https://")) for url in task.start_urls))

    def test_cli_selection_is_shared_by_single_range_and_site(self):
        parser = build_parser()
        repository = TaskRepository()
        self.assertEqual(
            select_tasks(parser.parse_args(["--task-id", "261", "261"]), repository), [261]
        )
        self.assertEqual(
            select_tasks(parser.parse_args(["--task-range", "0", "2"]), repository), [0, 1, 2]
        )
        args = parser.parse_args(["--site", "shopping", "--limit", "2"])
        self.assertEqual(len(select_tasks(args, repository)), 2)
        with self.assertRaises(ValueError):
            select_tasks(parser.parse_args(["--task-id", "99999"]), repository)

    def test_batch_continues_after_error_and_links_the_correct_attempt_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = SimpleNamespace(
                assets=SimpleNamespace(directory=root / "attempt-261"),
                run=Mock(side_effect=RuntimeError("Provider unavailable")),
            )
            succeeded = SimpleNamespace(
                run=Mock(return_value={"evaluation": {"status": "success"}})
            )
            output = io.StringIO()
            with (
                patch("skilltracer.llm.build_multi_llms", return_value={}),
                patch("skilltracer.runtime.workflow.Workflow", side_effect=[failed, succeeded]),
                patch("skilltracer.cli.logger.exception"),
                redirect_stdout(output),
            ):
                status = main(
                    [
                        "--task-id",
                        "261",
                        "274",
                        "--results-dir",
                        str(root / "results"),
                        "--state-dir",
                        str(root / "state"),
                    ]
                )
            self.assertEqual(status, 1)
            self.assertEqual(succeeded.run.call_count, 1)
            summary = json.loads(output.getvalue())
            self.assertEqual(
                (summary["completed"], summary["success"], summary["errors"]), (1, 1, 1)
            )
            error = json.loads((root / "results/shopping/errors/261.json").read_text())
            self.assertEqual(error["assets_dir"], str(root / "attempt-261"))

    def test_model_roles_match_the_active_agents(self):
        with patch("skilltracer.llm.build_llm", return_value=lambda prompt: "ok"):
            roles = build_multi_llms({"default_model": "openai/gpt-6-luna-pro"})
        self.assertEqual(set(roles), set(ROLES))
        with self.assertRaises(ValueError):
            build_multi_llms({"planner": lambda prompt: "ok"})

    def test_unfenced_plan_and_evidence_parse_without_consuming_evidence_json(self):
        obj = {
            "nodes": [
                {
                    "node_id": "n1",
                    "action": "click(target_id)",
                    "description": 'Read the {result} and "details".',
                }
            ],
            "edges": [],
        }
        text, evidence = split_plan_and_evidence(
            json.dumps(obj) + '\nevidence: [863] menuitem "Electronics" {extra}'
        )
        self.assertEqual(Plan.from_symbolic(text).get("nodes"), obj["nodes"])
        self.assertEqual(evidence.strip(), '[863] menuitem "Electronics" {extra}')
        with self.assertRaises(ValueError):
            Plan.from_symbolic(split_plan_and_evidence("No plan\nevidence: [1] heading")[0])

    def test_skip_existing_uses_the_tasks_domain_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shopping").mkdir()
            (root / "shopping/261.json").write_text("{}")
            (root / "274.json").write_text("{}")
            output = io.StringIO()
            with (
                patch("skilltracer.llm.build_multi_llms", return_value={}),
                patch("skilltracer.runtime.workflow.Workflow") as workflow,
                redirect_stdout(output),
            ):
                workflow.return_value.run.return_value = {"evaluation": {"status": "success"}}
                self.assertEqual(main(["--task-id", "261", "274", "--results-dir", str(root)]), 0)
            self.assertEqual(workflow.call_count, 1)
            self.assertEqual(workflow.call_args.args[1], 274)
            self.assertEqual(json.loads(output.getvalue())["skipped"], 1)

    def test_reasoning_model_omits_unsupported_temperature(self):
        options = completion_options("openai/gpt-6-luna-pro", 0.3, 4096)
        self.assertNotIn("temperature", options)
        self.assertEqual(options["max_tokens"], 4096)

    def test_context_is_substituted_once_without_rewriting_user_text(self):
        text = "literal $subgoal and {json}"
        prompt = Agent(None, "planner", action_set="", type_set="").build_prompt(
            "planner",
            query=text,
            subgoal="changed",
            axtree="",
            reuse_instructions="",
            reuse_fields="",
            past_plan="",
            plan_status=True,
            executed_plan="",
            history_plan="",
            history_actions="",
            active_memory="",
            task_description="",
        )
        self.assertIn(text, prompt)
        self.assertEqual(
            set(inspect.signature(SubgoalGenerator.generate).parameters),
            {
                "self",
                "task",
                "history_subgoals",
                "history_plan",
                "history_actions",
                "subgoal_memory_text",
                "current_axtree",
            },
        )
        for method in [Planner.plan, Executor.execute]:
            self.assertNotIn("hint", inspect.signature(method).parameters)

    def test_prompt_resources_remain_compact(self):
        prompts = list((ROOT / "skilltracer/agents/prompts").glob("*.md"))
        self.assertEqual(len(prompts), 8)
        self.assertTrue(all(len(prompt.read_text().split()) < 300 for prompt in prompts))

    def test_domain_state_starts_empty_without_copying_historical_experience(self):
        with tempfile.TemporaryDirectory() as directory:
            repositories = Repositories.open(directory, "map")
            task = repositories.load_task("Browse headphones", 8)
            self.assertEqual(task.skills, [])
            self.assertEqual(task.long_term, [])
            self.assertEqual(task.subgoal_entries, [])
            self.assertEqual(set(path.name for path in Path(directory).iterdir()), {"map"})
            self.assertTrue(all(not values for values in repositories.components.load().values()))

    def test_json_and_line_repairs_normalize_to_same_signal(self):
        signal = {
            "tool": "REPLACE_ACTION",
            "arguments": {
                "node_id": "n3",
                "new_action": "send_msg_to_user(result_text)",
            },
        }
        parsed, _ = parse_repair(
            "```json\n"
            + json.dumps(
                {
                    "error_node": "n3",
                    "repair_signal": signal,
                }
            )
            + "\n```"
        )
        self.assertEqual(parsed["repair_signal"], signal)
        line_response = (
            "'''summary\nerror_node: n3\nerror_evidence\nevidence: no element id\n"
            "summary: use observed text\nrepair_tool: REPLACE_ACTION\narguments:\n"
            "node_id: n3\nnew_action: send_msg_to_user(result_text)\n'''"
        )
        parsed, _ = parse_repair(line_response)
        self.assertEqual(parsed["error_node"], "n3")
        self.assertEqual(parsed["repair_signal"], signal)

    def test_repaired_prefix_stops_before_failed_node(self):
        bound = "<plan components>:\n'''n3: click('3')'''\n'''n8: click('8')'''\n<context>:"
        prefix = slice_filled_plan_before_node(bound, "n8")
        self.assertIn("n3", prefix)
        self.assertNotIn("n8", prefix)

    def test_duplicate_plan_nodes_are_rejected(self):
        with self.assertRaises(ValueError):
            Plan.from_symbolic(
                json.dumps(
                    {
                        "nodes": [
                            {"node_id": "n1", "action": "click(a)"},
                            {"node_id": "n1", "action": "click(b)"},
                        ]
                    }
                )
            )

    def test_import_and_help_need_no_credentials(self):
        code = (
            "from unittest.mock import patch; import os; "
            "patch('dotenv.load_dotenv').start(); "
            "os.environ.pop('OPENAI_API_KEY', None); "
            "os.environ.pop('OPENROUTER_API_KEY', None); "
            "import skilltracer.runtime.workflow; "
            "from skilltracer.cli import main; main(['--help'])"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_package_uses_workspace_for_configuration_and_output(self):
        environment = {key: value for key, value in os.environ.items() if key != "SKILLTRACER_HOME"}
        code = (
            "from pathlib import Path; from skilltracer.config import ROOT, DATA_DIR; "
            "assert ROOT == Path.cwd(); "
            "assert (DATA_DIR / 'tasks/261.json').is_file()"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_archive_excludes_private_and_generated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = build_release(Path(directory) / "source.zip")
            with ZipFile(archive) as stream:
                names = stream.namelist()
        self.assertIn("SkillTracer/.env.example", names)
        self.assertIn("SkillTracer/skilltracer/runtime/workflow.py", names)
        self.assertIn("SkillTracer/skilltracer/data/tasks/261.json", names)
        for name in names:
            self.assertNotIn(".venv/", name)
            self.assertNotIn(".local/", name)
            self.assertNotIn("backup/", name)
            self.assertNotIn("__pycache__/", name)
            self.assertNotIn(":Zone.Identifier", name)
            self.assertNotEqual(Path(name).name, ".env")


if __name__ == "__main__":
    unittest.main()
