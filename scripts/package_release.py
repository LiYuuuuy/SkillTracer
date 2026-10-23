#!/usr/bin/env python3
"""Build a source archive using an explicit allowlist of release files."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATHS = (
    "README.md",
    "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md",
    "index.html",
    "overview.png",
    "case_study.png",
    "result.png",
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock.txt",
    ".gitignore",
    ".env.example",
    "skilltracer",
    "browsergym/core",
    "scripts",
    "tests",
    "docs",
)


def release_files(root=ROOT):
    for name in RELEASE_PATHS:
        path = root / name
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root)
            if "__pycache__" in relative.parts or ":Zone.Identifier" in candidate.name:
                continue
            if candidate.suffix in {".pyc", ".pyo", ".log"} or candidate.name == ".env":
                continue
            yield candidate


def build_release(output, root=ROOT):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in release_files(root):
            archive.write(path, Path("SkillTracer") / path.relative_to(root))
    return output.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT.parent / "dist/skilltracer-source.zip")
    args = parser.parse_args()
    print(build_release(args.output))


if __name__ == "__main__":
    main()
