"""Inspect domain banks and archive legacy unscoped state without guessing domains."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from .config import ROOT, SITE_URLS
from .memory.components import ComponentRegistry
from .memory.experience import LongTermMemory
from .memory.skills import SkillOutputParseError, SkillRepository
from .memory.subgoals import SubgoalMemory
from .runtime.repositories import Repositories
from .storage import BANK_FILES, STATE_KIND, fingerprint, utc_now, write_json


def inspect_state(state_dir):
    root = Path(state_dir).expanduser().resolve()
    domains = {}
    for domain in SITE_URLS:
        directory = root / domain
        metadata_path = directory / "manifest.json"
        if not metadata_path.exists():
            if any((directory / name).exists() for name in BANK_FILES.values()):
                domains[domain] = {
                    "directory": str(directory),
                    "error": "Domain bank files exist without domain metadata.",
                }
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("kind") != STATE_KIND or metadata.get("domain") != domain:
                raise ValueError("Domain metadata does not match its directory.")
            if not all((directory / name).is_file() for name in BANK_FILES.values()):
                raise ValueError("One or more domain banks are missing.")
            domains[domain] = {
                "directory": str(directory),
                "skills": len(SkillRepository(str(directory / "skills.txt")).read_all()),
                "long_term_memories": len(
                    LongTermMemory.export_long_term_memories_from_txt(str(directory / "memory.txt"))
                ),
                "subgoal_experiences": len(
                    SubgoalMemory(str(directory / "subgoals.txt")).read_all()
                ),
                "component_types": sum(
                    len(types)
                    for types in ComponentRegistry(str(directory / "components.txt"))
                    .load()
                    .values()
                ),
            }
        except (OSError, ValueError, SkillOutputParseError) as exc:
            domains[domain] = {"directory": str(directory), "error": str(exc)}
    return {
        "state_root": str(root),
        "domains": domains,
        "unscoped_files": [name for name in BANK_FILES.values() if (root / name).exists()],
    }


def _read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _committed(metadata, bank):
    flags = metadata.get("bank_commits")
    return (
        bool(flags.get(bank))
        if isinstance(flags, dict)
        else bool(metadata.get("learning_committed"))
    )


def _recovery_sources(state_dir, results_dir):
    sources = []
    if not results_dir.exists():
        return sources
    for path in results_dir.rglob("manifest.json"):
        metadata = _read(path, {})
        state = metadata.get("state_directory")
        domain = metadata.get("domain") or metadata.get("site")
        if not state or Path(state).resolve() != state_dir or domain not in SITE_URLS:
            continue
        if metadata.get("domain", domain) != metadata.get("site", domain):
            continue
        sources.append((path, metadata))
    return sorted(sources, key=lambda item: (item[1].get("started_at", ""), str(item[0])))


def _recover_attempt(repositories, path, metadata, aliases):
    directory = path.parent
    task_id = metadata.get("task_id", "unknown")
    recovered = {bank: 0 for bank in BANK_FILES}
    if _committed(metadata, "skills"):
        data = _read(directory / "skills.json", {})
        skills = repositories.skills.read_all()
        for item in data.get("produced", data.get("learned", [])):
            original_id = str(item["id"])
            alias = aliases.get(original_id)
            value = {**item, "id": alias or "legacy-import-" + uuid4().hex}
            skills = SkillRepository.upsert_skill(skills, value)
            normalized = (
                next(skill for skill in skills if skill["id"] == alias) if alias else skills[-1]
            )
            aliases[original_id] = normalized["id"]
            repositories.skills.write_back(skills)
            repositories.record_commit(
                "skills",
                task_id,
                directory / "skills.json",
                [normalized],
                operation="legacy_import",
                source_skill_id=original_id,
            )
            recovered["skills"] += 1
    if _committed(metadata, "long_term_memory"):
        data = _read(directory / "long_term_memory.json", {})
        memories = LongTermMemory.export_long_term_memories_from_txt(str(repositories.memory_path))
        known = {fingerprint(item.to_dict()) for item in memories}
        additions = []
        for item in data.get("produced", data.get("learned", [])):
            key = fingerprint(item)
            if key not in known:
                additions.append(item)
                known.add(key)
        if additions:
            memories.extend(LongTermMemory.from_dict(item) for item in additions)
            LongTermMemory.import_long_term_memories_to_txt(memories, str(repositories.memory_path))
            repositories.record_commit(
                "long_term_memory",
                task_id,
                directory / "long_term_memory.json",
                additions,
                operation="legacy_import",
            )
            recovered["long_term_memory"] = len(additions)
    if _committed(metadata, "components"):
        data = _read(directory / "components.json", {})
        additions = data.get("produced")
        if additions is None:
            additions = [
                {"purpose": purpose, "component_type": name}
                for purpose, names in data.get("learned", {}).items()
                for name in names
            ]
        inserted = [
            item
            for item in additions
            if repositories.components.update(item["purpose"], item["component_type"])
        ]
        if inserted:
            repositories.components.write_back()
            repositories.record_commit(
                "components",
                task_id,
                directory / "components.json",
                inserted,
                operation="legacy_import",
            )
            recovered["components"] = len(inserted)
    if _committed(metadata, "subgoal_memory"):
        data = _read(directory / "subgoal_memory.json", {})
        additions = data.get("produced")
        if additions is None:
            task = _read(directory / "progress.json", {}).get("query")
            additions = []
            for item in data.get("staged", []):
                guidance = " ".join(
                    str(value).strip()
                    for value in item.get("guidances", [])
                    if value is not None and str(value).strip().lower() not in {"", "null"}
                )
                if task and guidance:
                    additions.append(
                        {"task": task, "subgoal": item["subgoal"], "guidance": guidance}
                    )
        for item in additions:
            repositories.subgoals.write_back(
                repositories.subgoals.read_all(),
                item["task"],
                [item["subgoal"]],
                [[item["guidance"]]],
            )
            repositories.record_commit(
                "subgoal_memory",
                task_id,
                directory / "subgoal_memory.json",
                [item],
                operation="legacy_import",
            )
        recovered["subgoal_memory"] = len(additions)
    return recovered


def migrate_state(state_dir, backup_dir, results_dir=None):
    root = Path(state_dir).expanduser().resolve()
    if _read(root / "manifest.json", {}).get("kind") == STATE_KIND:
        raise ValueError(f"--state-dir expects a root, not a domain directory: use {root.parent}")
    legacy = [root / name for name in BANK_FILES.values() if (root / name).is_file()]
    if not legacy:
        return {
            "state_root": str(root),
            "archived_files": 0,
            "recovered": {},
            "status": "already_scoped_or_empty",
        }
    results = Path(results_dir).expanduser().resolve() if results_dir else root.parent / "results"
    sources = _recovery_sources(root, results)
    destination = Path(backup_dir).expanduser().resolve() / (root.name + "-" + uuid4().hex[:12])
    destination.mkdir(parents=True, exist_ok=False)
    entries = []
    for source in legacy:
        target = destination / source.name
        shutil.copy2(source, target)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise OSError(f"Archive verification failed: {source}")
        entries.append(
            {
                "original_path": str(source),
                "file": target.name,
                "bytes": source.stat().st_size,
                "sha256": digest,
            }
        )
    report = {
        "schema_version": 2,
        "state_root": str(root),
        "timestamp": utc_now(),
        "backup_directory": str(destination),
        "files": entries,
        "recovery_sources": [str(path) for path, _ in sources],
        "recovered": {},
        "status": "archived",
    }
    write_json(destination / "migration.json", report)
    aliases = {}
    staging = destination / "staging"
    changed_domains = []
    try:
        for path, metadata in sources:
            domain = metadata.get("domain") or metadata["site"]
            if not (staging / domain).exists() and (root / domain).exists():
                shutil.copytree(root / domain, destination / "scoped_before" / domain)
                shutil.copytree(root / domain, staging / domain)
            repositories = Repositories.open(staging, domain)
            counts = _recover_attempt(repositories, path, metadata, aliases.setdefault(domain, {}))
            total = report["recovered"].setdefault(domain, {bank: 0 for bank in BANK_FILES})
            for bank, count in counts.items():
                total[bank] += count
        for domain in report["recovered"]:
            target = root / domain
            changed_domains.append(domain)
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(staging / domain), str(target))
        for source in legacy:
            source.unlink()
        if staging.exists():
            staging.rmdir()
        report.update({"status": "complete", "archived_files": len(entries)})
    except Exception as exc:
        for domain in changed_domains:
            target = root / domain
            if target.exists():
                shutil.rmtree(target)
            previous = destination / "scoped_before" / domain
            if previous.exists():
                shutil.copytree(previous, target)
        for source in legacy:
            shutil.copy2(destination / source.name, source)
        report.update({"status": "recovery_failed", "error": str(exc)})
        raise
    finally:
        write_json(destination / "migration.json", report)
    write_json(
        root / "migration.json",
        {
            "backup_directory": str(destination),
            "report": str(destination / "migration.json"),
            "recovered": report["recovered"],
            "status": report["status"],
        },
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "migrate"))
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local/state")
    parser.add_argument(
        "--results-dir", type=Path, help="Locate attributed task assets when migrating."
    )
    parser.add_argument("--backup-dir", type=Path, default=ROOT.parent / "backup/assets")
    args = parser.parse_args(argv)
    if args.command == "inspect":
        report = inspect_state(args.state_dir)
    else:
        report = migrate_state(args.state_dir, args.backup_dir, args.results_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
