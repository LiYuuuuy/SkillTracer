"""Verify domain boundaries, producer-only storage and safe legacy recovery."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from skilltracer.assets import inspect_state, migrate_state
from skilltracer.memory.active import ActiveMemory
from skilltracer.memory.experience import LongTermMemory
from skilltracer.memory.skills import SkillRepository
from skilltracer.planning.plan import Plan
from skilltracer.runtime.assets import TaskAssets
from skilltracer.runtime.learning import LearningService
from skilltracer.runtime.repositories import Repositories
from skilltracer.runtime.results import ArtifactStore
from skilltracer.runtime.state import SubgoalState
from skilltracer.storage import BANK_FILES, write_json


def skill(identifier="s1", name="Produced skill"):
    return {"id": identifier, "name": name, "description": name, "plan": {"nodes": []}}


def memory(label="Produced repair"):
    return LongTermMemory(label, "Find result", {}, "Unavailable", [], {"insight": label})


class DomainAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def repositories(self, domain="shopping", root=None):
        return Repositories.open(root or self.root / "state", domain)

    def assets(self, repositories, debug=False):
        store = ArtifactStore(self.root / "results", 274, repositories.domain, debug=debug)
        task = repositories.load_task("Find result", 8)
        return TaskAssets(
            store, task, ActiveMemory(), repositories, repositories.domain, "test", []
        )

    def test_domains_start_empty_and_keep_all_four_banks_separate(self):
        shopping = self.repositories()
        admin = self.repositories("shopping_admin")
        for repositories in (shopping, admin):
            task = repositories.load_task("Find result", 8)
            self.assertEqual(task.skills, [])
            self.assertEqual(task.long_term, [])
            self.assertEqual(task.subgoal_entries, [])
            self.assertFalse(any(repositories.components.load().values()))
        shopping.skills.write_back([skill(name="Shopping only")])
        LongTermMemory.import_long_term_memories_to_txt(
            [memory("Shopping only")], shopping.memory_path
        )
        shopping.subgoals.write_back([], "Find result", ["Read price"], [["Shopping only"]])
        shopping.components.update("search", "shopping_only")
        shopping.components.write_back()
        admin.skills.write_back([skill(name="Admin only")])
        shopping_task = self.repositories().load_task("Find result", 8)
        admin_task = self.repositories("shopping_admin").load_task("Find result", 8)
        self.assertEqual(shopping_task.skills[0]["id"], admin_task.skills[0]["id"])
        self.assertEqual(admin_task.skills[0]["name"], "Admin only")
        self.assertEqual(admin_task.long_term_context, "")
        self.assertEqual(admin_task.subgoal_context, "")
        self.assertIn("Shopping only", shopping_task.long_term_context)
        self.assertIn("Shopping only", shopping_task.subgoal_context)
        self.assertFalse(any(self.repositories("shopping_admin").components.load().values()))

    def test_wrong_scope_missing_bank_and_unscoped_data_are_rejected(self):
        for domain in ("../shopping", "unknown", "shopping/admin"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                self.repositories(domain)
        repositories = self.repositories()
        with self.assertRaisesRegex(ValueError, "expects a root"):
            Repositories.open(repositories.directory, "shopping")
        with self.assertRaisesRegex(ValueError, "expects a root"):
            migrate_state(repositories.directory, self.root / "backup")
        (repositories.directory / "memory.txt").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.repositories()
        self.assertFalse((repositories.directory / "memory.txt").exists())
        leaf = self.root / "wrong" / "shopping"
        leaf.mkdir(parents=True)
        write_json(leaf / "manifest.json", {"kind": "skilltracer.domain_state", "domain": "map"})
        with self.assertRaisesRegex(ValueError, "scope"):
            self.repositories(root=leaf.parent)
        root = self.root / "legacy"
        root.mkdir()
        (root / "skills.txt").write_text("Unattributed history", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unscoped legacy"):
            self.repositories(root=root)

    def test_task_asset_size_does_not_grow_with_loaded_banks(self):
        sizes = []
        for count in (0, 1000):
            repositories = self.repositories(root=self.root / f"bank{count}")
            repositories.skills.write_back([skill(f"s{i}", f"Old skill {i}") for i in range(count)])
            LongTermMemory.import_long_term_memories_to_txt(
                [memory(f"Old repair {i}") for i in range(count)], repositories.memory_path
            )
            repositories.subgoals._dump_jsonl(
                [
                    {
                        "task": f"Find result {i}",
                        "subgoal_guidance": [{"subgoal": "Read", "guidance": str(i)}],
                    }
                    for i in range(count)
                ]
            )
            for i in range(count):
                repositories.components.update("search", f"old_type_{i}")
            repositories.components.write_back()
            with self.assets(repositories) as assets:
                assets.record_model_call(
                    "planner", "planner.md", "Loaded context" * (count + 1), "Done"
                )
                assets.status = "failure"
            for bank, filename in TaskAssets.BANK_ASSETS.items():
                payload = json.loads((assets.directory / filename).read_text())
                self.assertEqual(payload["produced"], [], bank)
                self.assertNotIn("initial", payload)
                self.assertNotIn("current", payload)
                self.assertNotIn("candidates", payload)
            calls = (assets.directory / "model_calls.jsonl").read_text()
            self.assertNotIn("prompt", json.loads(calls))
            self.assertNotIn("Loaded context", calls)
            self.assertFalse((repositories.directory / "provenance.jsonl").exists())
            sizes.append(sum(path.stat().st_size for path in assets.directory.iterdir()))
        self.assertLess(abs(sizes[1] - sizes[0]), 100)

    def test_debug_prompts_require_explicit_opt_in(self):
        with self.assets(self.repositories(), debug=True) as assets:
            assets.record_model_call("planner", "planner.md", "Debug context", "Done")
        record = json.loads((assets.directory / "model_calls.jsonl").read_text())
        self.assertEqual(record["prompt"], "Debug context")
        self.assertTrue(
            json.loads((assets.directory / "manifest.json").read_text())["prompts_recorded"]
        )

    def test_produced_entities_have_domain_and_commit_provenance(self):
        repositories = self.repositories()
        repositories.skills.write_back([skill(name="Old loaded skill")])
        LongTermMemory.import_long_term_memories_to_txt(
            [memory("Old loaded repair")], repositories.memory_path
        )
        with self.assets(repositories) as assets:
            updated = skill(name="Refined skill")
            added = memory().to_dict()
            assets.store.record("skill_induced", skills=[updated])
            assets.store.record("long_term_memory_created", entries=[added])
            assets.task.candidate_skills = [updated]
            assets.task.long_term.append(LongTermMemory.from_dict(added))
            assets.task.subgoals = ["Find result"]
            assets.task.guidances = [["Null", "Use the observed search box"]]
            repositories.persist_success(assets.task, assets.store.committed)
        self.assertEqual(
            json.loads((assets.directory / "skills.json").read_text())["produced"], [updated]
        )
        self.assertEqual(
            json.loads((assets.directory / "long_term_memory.json").read_text())["produced"],
            [added],
        )
        records = [
            json.loads(line)
            for line in (repositories.directory / "provenance.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            {item["bank"] for item in records}, {"skills", "long_term_memory", "subgoal_memory"}
        )
        for item in records:
            self.assertEqual(item["domain"], "shopping")
            self.assertEqual(item["task_id"], "274")
            self.assertTrue(Path(item["source"]).is_file())
            self.assertEqual(set(item["entities"][0]), {"id", "sha256"})

    def test_types_are_journaled_on_the_first_commit_and_only_once(self):
        repositories = self.repositories()
        plan = Plan(
            {
                "nodes": [
                    {
                        "node_id": "n1",
                        "purpose": "search",
                        "component_type": "search_box",
                        "action": "fill(target, value)",
                        "description": "Search",
                    }
                ],
                "edges": [],
            }
        )
        subgoal = SubgoalState("Search", "", True, plan=plan, complete=True)
        with self.assets(repositories) as assets:
            learning = LearningService(repositories, Mock(), Mock(), artifacts=assets.store)
            for _ in range(2):
                learning.finish_subgoal(assets.task, subgoal)
            assets.status = "failure"
        records = [
            json.loads(line)
            for line in (repositories.directory / "provenance.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["bank"], "components")
        self.assertEqual(
            json.loads((assets.directory / "components.json").read_text())["produced"],
            [{"purpose": "search", "component_type": "search_box"}],
        )
        self.assertIn("search_box", self.repositories().components.load()["search"])

    def legacy(self):
        root = self.root / "legacy"
        root.mkdir()
        SkillRepository(str(root / "skills.txt")).write_back(
            [skill("s94", "Unattributed old skill")]
        )
        for filename in ("memory.txt", "subgoals.txt", "components.txt"):
            (root / filename).write_text("Unattributed old history", encoding="utf-8")
        return root

    def attempt(self, root, name, domain="shopping", committed=True, **files):
        directory = self.root / "legacy_results" / name
        directory.mkdir(parents=True)
        write_json(
            directory / "manifest.json",
            {
                "state_directory": str(root),
                "site": domain,
                "task_id": 274,
                "started_at": name,
                "bank_commits": {bank: committed for bank in BANK_FILES},
            },
        )
        for filename, payload in files.items():
            write_json(directory / filename, payload)
        return directory

    def test_migration_recovers_only_attributed_production_by_domain(self):
        root = self.legacy()
        originals = {name: (root / name).read_bytes() for name in BANK_FILES.values()}
        self.attempt(
            root,
            "1",
            **{
                "skills.json": {
                    "initial": [skill("s94")],
                    "learned": [skill("s4", "Shopping generated")],
                },
                "long_term_memory.json": {"learned": [memory().to_dict()]},
                "components.json": {"learned": {"search": ["search_box"]}},
                "subgoal_memory.json": {
                    "staged": [{"subgoal": "Search", "guidances": ["Null", "Enter query"]}]
                },
                "progress.json": {"query": "Find result"},
            },
        )
        self.attempt(root, "2", **{"skills.json": {"learned": [skill("s4", "Shopping refined")]}})
        self.attempt(
            root,
            "3",
            domain="shopping_admin",
            **{"skills.json": {"produced": [skill("s4", "Admin generated")]}},
        )
        failed = self.attempt(
            root,
            "4",
            committed=False,
            **{
                "skills.json": {"produced": [skill("s5", "Uncommitted candidate")]},
                "components.json": {"produced": [{"purpose": "filter", "component_type": "facet"}]},
            },
        )
        metadata = json.loads((failed / "manifest.json").read_text())
        metadata["bank_commits"]["components"] = True
        write_json(failed / "manifest.json", metadata)
        self.attempt(
            self.root / "unrelated",
            "5",
            **{"skills.json": {"produced": [skill(name="Wrong state")]}},
        )
        report = migrate_state(root, self.root / "backup", self.root / "legacy_results")
        self.assertEqual(report["status"], "complete")
        for entry in report["files"]:
            archived = Path(report["backup_directory"]) / entry["file"]
            self.assertEqual(archived.read_bytes(), originals[entry["file"]])
            self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(), entry["sha256"])
        shopping = self.repositories(root=root)
        admin = self.repositories("shopping_admin", root=root)
        self.assertEqual(shopping.skills.read_all(), [skill("s1", "Shopping refined")])
        self.assertEqual(admin.skills.read_all(), [skill("s1", "Admin generated")])
        self.assertEqual(len(shopping.load_task("Find result", 8).long_term), 1)
        self.assertEqual(
            shopping.subgoals.read_all()[0]["subgoal_guidance"][0]["guidance"], "Enter query"
        )
        self.assertIn("facet", shopping.components.load()["filter"])
        self.assertEqual(inspect_state(root)["unscoped_files"], [])
        self.assertEqual(
            migrate_state(root, self.root / "backup")["status"], "already_scoped_or_empty"
        )

    def test_unattributed_banks_are_archived_without_guessing_a_domain(self):
        root = self.legacy()
        report = migrate_state(root, self.root / "backup")
        self.assertEqual(report["recovered"], {})
        self.assertEqual(report["archived_files"], 4)
        self.assertEqual(inspect_state(root)["domains"], {})
        self.assertEqual(self.repositories(root=root).skills.read_all(), [])

    def test_invalid_recovery_preserves_legacy_and_existing_scoped_banks(self):
        root = self.legacy()
        original = (root / "skills.txt").read_bytes()
        directory = root / "shopping"
        directory.mkdir()
        write_json(
            directory / "manifest.json",
            {"kind": "skilltracer.domain_state", "domain": "shopping"},
        )
        for name in BANK_FILES.values():
            (directory / name).touch()
        SkillRepository(str(directory / "skills.txt")).write_back([skill(name="Existing scoped")])
        existing = (directory / "skills.txt").read_bytes()
        self.attempt(root, "broken", **{"skills.json": {"produced": [{"name": "Missing ID"}]}})
        with self.assertRaises(KeyError):
            migrate_state(root, self.root / "backup", self.root / "legacy_results")
        self.assertEqual((root / "skills.txt").read_bytes(), original)
        self.assertEqual((directory / "skills.txt").read_bytes(), existing)
        reports = list((self.root / "backup").glob("*/migration.json"))
        self.assertEqual(json.loads(reports[0].read_text())["status"], "recovery_failed")


if __name__ == "__main__":
    unittest.main()
