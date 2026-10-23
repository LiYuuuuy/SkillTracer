#!/usr/bin/env python3
"""Start existing WebArena containers without recreating their data."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skilltracer.config import SITE_URLS

CONTAINERS = {
    "shopping": ["shopping"],
    "shopping_admin": ["shopping_admin"],
    "reddit": ["forum"],
    "gitlab": ["gitlab"],
    "map": ["openstreetmap-website-db-1", "openstreetmap-website-web-1"],
}


def find_docker(explicit=None):
    candidates = [
        explicit or os.getenv("DOCKER_CLI"),
        shutil.which("docker"),
        shutil.which("docker.exe"),
    ]
    for command in dict.fromkeys(c for c in candidates if c):
        try:
            result = subprocess.run(
                [command, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError(
        "Docker daemon is unavailable. Start Docker Desktop (and WSL integration), or set DOCKER_CLI to docker.exe."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", nargs="+", choices=CONTAINERS, default=["shopping"])
    parser.add_argument("--docker", help="Path to Docker CLI; Windows docker.exe works from WSL")
    args = parser.parse_args()
    docker = find_docker(args.docker)
    for site in args.sites:
        for name in CONTAINERS[site]:
            result = subprocess.run(
                [docker, "inspect", name], capture_output=True, text=True, timeout=20
            )
            if result.returncode:
                raise RuntimeError(
                    f"Missing container {name}. Load the official WebArena images and create containers first; see README.md."
                )
            info = json.loads(result.stdout)[0]
            if not info["State"]["Running"]:
                subprocess.run([docker, "start", name], check=True, timeout=120)
            print(f"{site}: {name} started, URL {SITE_URLS[site]}", flush=True)
    print(
        "Cold starts on an external HDD can take several minutes. Verify with scripts/check_local.py before running tasks."
    )


if __name__ == "__main__":
    main()
