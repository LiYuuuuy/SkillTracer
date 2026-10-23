import os
from typing import Dict, List, Optional, Set


class ComponentRegistry:
    """
    UI component type registry (cached, instance-based).

    - load(): read from the configured repository once into memory
    - get_type_set(): return prompt-ready string from cached data
    - update(): update cached data
    - write_back(): persist cached data back to the configured repository
    """

    PURPOSES: List[str] = [
        "search",
        "filter",
        "navigation",
        "commit_input",
        "commit_output",
        "state_commit",
    ]

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path
        self._component_map: Dict[str, Set[str]] = {p: set() for p in self.PURPOSES}
        self._loaded: bool = False

    # -------------------------
    # 1) Load once (cached)
    # -------------------------
    def load(self, force_reload: bool = False) -> Dict[str, Set[str]]:
        """
        Load component types from repo file into cache.

        Args:
            force_reload: if True, reload from disk even if already loaded.
        """
        if self._loaded and not force_reload:
            return self._component_map

        # reset
        self._component_map = {p: set() for p in self.PURPOSES}

        if not os.path.exists(self.repo_path):
            self._loaded = True
            return self._component_map

        current_purpose: Optional[str] = None

        with open(self.repo_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue

                # Section header: "# purpose"
                if line.startswith("#"):
                    purpose = line[1:].strip()
                    current_purpose = purpose if purpose in self.PURPOSES else None
                    continue

                # ignore lines outside known sections
                if current_purpose is None:
                    continue

                self._component_map[current_purpose].add(line)

        self._loaded = True
        return self._component_map

    # -------------------------
    # 2) Promptify
    # -------------------------
    def get_type_set(self) -> str:
        """
        Convert cached type set into prompt-ready string.
        Will auto-load once if not loaded.
        """
        if not self._loaded:
            self.load()

        lines: List[str] = []
        for purpose in self.PURPOSES:
            lines.append(f"{purpose}:")
            for t in sorted(self._component_map.get(purpose, set())):
                lines.append(f"  - {t}")
            lines.append("")
        return "\n".join(lines).strip()

    # -------------------------
    # 3) Update in memory
    # -------------------------
    def update(self, purpose: str, component_type: str) -> bool:
        """
        Update registry cache.

        Returns:
            True if inserted (new),
            False if skipped (already existed) or invalid input.
        """
        if purpose not in self.PURPOSES:
            return False

        if not component_type or not isinstance(component_type, str):
            return False

        if not self._loaded:
            self.load()

        before = len(self._component_map[purpose])
        self._component_map[purpose].add(component_type.strip())
        after = len(self._component_map[purpose])
        return after > before

    # -------------------------
    # 4) Write back (atomic)
    # -------------------------
    def write_back(self) -> None:
        """
        Persist cached registry to repo file.
        Will auto-load once if not loaded (so we don't accidentally wipe file).
        """
        if not self._loaded:
            self.load()

        os.makedirs(os.path.dirname(self.repo_path), exist_ok=True)

        lines: List[str] = []
        for purpose in self.PURPOSES:
            lines.append(f"# {purpose}")
            for t in sorted(self._component_map.get(purpose, set())):
                lines.append(t)
            lines.append("")

        content = "\n".join(lines).strip() + "\n"

        tmp_path = self.repo_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, self.repo_path)
