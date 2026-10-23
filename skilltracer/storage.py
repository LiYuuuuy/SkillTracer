"""Shared domain validation, bank filenames and content identifiers."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import SITE_URLS

BANK_FILES = {
    "skills": "skills.txt",
    "long_term_memory": "memory.txt",
    "subgoal_memory": "subgoals.txt",
    "components": "components.txt",
}
STATE_KIND = "skilltracer.domain_state"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def domain_directory(root, domain):
    if domain not in SITE_URLS:
        raise ValueError(f"Unsupported asset domain: {domain!r}")
    return Path(root).expanduser().resolve() / domain


def fingerprint(value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path, entries):
    """Replace a bank only after all entries have been serialized successfully."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            for entry in entries:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
