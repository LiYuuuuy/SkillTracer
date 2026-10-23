"""Load bundled task definitions and resolve configurable site origins."""

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import DATA_DIR, SITE_URLS

LEGACY_PORTS = {
    "7770": "shopping",
    "7780": "shopping_admin",
    "9999": "reddit",
    "8023": "gitlab",
    "3000": "map",
    "8888": "wikipedia",
    "4399": "homepage",
}


def resolve_start_url(raw_url):
    value = raw_url.strip()
    if value.startswith("None:"):
        value = "http://localhost:" + value[5:]
    elif value.split("/", 1)[0].isdigit():
        value = "http://localhost:" + value
    elif not value.startswith(("http://", "https://")):
        value = "http://" + value
    parsed = urlsplit(value)
    site = LEGACY_PORTS.get(str(parsed.port))
    if site:
        origin = urlsplit(SITE_URLS[site])
        return urlunsplit(
            (origin.scheme, origin.netloc, parsed.path, parsed.query, parsed.fragment)
        )
    return value


@dataclass(frozen=True)
class Task:
    task_id: int
    intent: str
    sites: tuple[str, ...]
    start_urls: tuple[str, ...]

    @property
    def start_url(self):
        return self.start_urls[0]

    @property
    def site(self):
        return self.sites[0]


class TaskRepository:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else DATA_DIR / "tasks"

    def ids(self):
        return sorted(
            int(path.stem) for path in self.directory.glob("*.json") if path.stem.isdigit()
        )

    def load(self, task_id):
        path = self.directory / f"{int(task_id)}.json"
        obj = json.loads(path.read_text(encoding="utf-8"))
        urls = tuple(
            resolve_start_url(url) for url in obj.get("start_url", "").split("|AND|") if url.strip()
        )
        sites = tuple(obj.get("sites", []))
        if not urls or not sites or not obj.get("intent"):
            raise ValueError(f"Incomplete task configuration: {path}")
        return Task(int(task_id), obj["intent"], sites, urls)

    def for_site(self, site):
        return [task_id for task_id in self.ids() if self.load(task_id).sites == (site,)]
