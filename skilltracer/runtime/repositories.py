"""Load only the current domain's banks; new domains start without experience."""

import json
from dataclasses import dataclass
from pathlib import Path

from skilltracer.memory.components import ComponentRegistry
from skilltracer.memory.experience import LongTermMemory
from skilltracer.memory.skills import SkillRepository
from skilltracer.memory.subgoals import SubgoalMemory
from skilltracer.storage import (
    BANK_FILES,
    STATE_KIND,
    domain_directory,
    fingerprint,
    utc_now,
    write_json,
)

from .state import TaskState


@dataclass
class Repositories:
    components: ComponentRegistry
    skills: SkillRepository
    subgoals: SubgoalMemory
    memory_path: Path
    root: Path
    domain: str
    directory: Path

    @classmethod
    def open(cls, state_dir, domain):
        root = Path(state_dir).expanduser().resolve()
        directory = domain_directory(root, domain)
        if (root / "manifest.json").exists():
            metadata = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if metadata.get("kind") == STATE_KIND:
                raise ValueError(
                    f"--state-dir expects a root, not a domain directory: use {root.parent}"
                )
        if any((root / name).exists() for name in BANK_FILES.values()):
            raise ValueError(
                f"Unscoped legacy banks exist in {root}. Archive them with "
                f"python -m skilltracer.assets migrate --state-dir {root}. "
                "They will not be loaded into a domain automatically."
            )
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if metadata.get("kind") != STATE_KIND or metadata.get("domain") != domain:
                raise ValueError(f"State scope does not match {domain!r}: {manifest_path}")
            missing = [name for name in BANK_FILES.values() if not (directory / name).is_file()]
            if missing:
                raise ValueError(f"Domain bank files are missing in {directory}: {missing}")
        else:
            if any(
                (directory / name).exists() and (directory / name).stat().st_size
                for name in BANK_FILES.values()
            ):
                raise ValueError(f"Nonempty banks without domain metadata: {directory}")
            for name in BANK_FILES.values():
                (directory / name).touch()
            write_json(
                manifest_path,
                {
                    "schema_version": 2,
                    "kind": STATE_KIND,
                    "domain": domain,
                    "created_at": utc_now(),
                    "initialization": "empty",
                    "banks": BANK_FILES,
                },
            )
        return cls(
            ComponentRegistry(str(directory / "components.txt")),
            SkillRepository(str(directory / "skills.txt")),
            SubgoalMemory(str(directory / "subgoals.txt")),
            directory / "memory.txt",
            root,
            domain,
            directory,
        )

    def record_commit(self, bank, task_id, source, entities, **details):
        if not entities:
            return
        record = {
            "schema_version": 2,
            "timestamp": utc_now(),
            "domain": self.domain,
            "bank": bank,
            "task_id": str(task_id),
            "source": str(source),
            "entities": [
                {"id": entity.get("id", fingerprint(entity)), "sha256": fingerprint(entity)}
                for entity in entities
            ],
            **details,
        }
        with (self.directory / "provenance.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_task(self, query, maximum_steps):
        skills = self.skills.read_all()
        entries = self.subgoals.read_all()
        matched = self.subgoals.retrieve_topk(query, 5, entries)
        long_term = LongTermMemory.export_long_term_memories_from_txt(str(self.memory_path))
        return TaskState(
            query=query,
            maximum_steps=maximum_steps,
            skills=skills,
            candidate_skills=list(skills),
            long_term=long_term,
            long_term_context=LongTermMemory.build_long_term_memory_prompt(long_term),
            subgoal_entries=entries,
            subgoal_context=self.subgoals.build_prompt(matched),
        )

    def persist_success(self, task, on_commit=None):
        self.skills.write_back(task.candidate_skills)
        if on_commit is not None:
            on_commit("skills")
        LongTermMemory.import_long_term_memories_to_txt(task.long_term, str(self.memory_path))
        if on_commit is not None:
            on_commit("long_term_memory")
        self.subgoals.write_back(
            task.subgoal_entries,
            task.query,
            task.subgoals,
            task.guidances,
        )
        if on_commit is not None:
            on_commit("subgoal_memory")
