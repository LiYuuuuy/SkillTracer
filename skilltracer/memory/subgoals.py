from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Tuple

from skilltracer.storage import write_jsonl


class SubgoalMemory:
    """
    Historical task and subgoal guidance for subgoal generation.

    Storage format:
      - JSON Lines file (one JSON object per line)
      - Each object schema:
        {
          "task": <str>,
          "subgoal_guidance": [
            {"subgoal": <str>, "guidance": <str>},
            ...
          ]
        }
    """

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    # ----------------------------
    # Read the complete subgoal experience repository.
    # ----------------------------
    def read_all(self) -> List[Dict[str, Any]]:
        """
        Read JSONL memory entries from repo file.
        Returns a list of dict entries.
        """
        if not os.path.exists(self.repo_path):
            return []

        entries: List[Dict[str, Any]] = []
        with open(self.repo_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # keep only minimally valid items
                    if (
                        isinstance(obj, dict)
                        and isinstance(obj.get("task"), str)
                        and isinstance(obj.get("subgoal_guidance"), list)
                    ):
                        entries.append(obj)
                except Exception:
                    # skip corrupted lines silently
                    continue
        return entries

    # -----------------------------------------
    # Merge completed subgoals and guidance into the repository.
    # -----------------------------------------
    def write_back(
        self,
        repo_entries: List[Dict[str, Any]],
        task: str,
        history_subgoals: List[str],
        subgoal_guidances: List[
            List[str]
        ],  # Aligned with history_subgoals; one list of guidance strings per subgoal.
    ) -> List[Dict[str, Any]]:
        """
        Update in-memory repo_entries with the current task's subgoal guidance,
        then persist to file.

        Rules:
        - One history subgoal maps to one guidance list[str].
        - Store only nonempty, non-Null guidance.
        - For each subgoal, concatenate its guidance list[str] into ONE sentence.
        - If all guidance is Null or empty, leave the repository unchanged.
        - One task maps to one memory entry; if exists, merge/overwrite guidance pairs.
        """

        def _is_null_like(x: Any) -> bool:
            if x is None:
                return True
            if isinstance(x, str) and x.strip().lower() == "null":
                return True
            if isinstance(x, str) and not x.strip():
                return True
            return False

        # build filtered pairs: one subgoal -> one concatenated guidance string
        pairs: List[Dict[str, str]] = []
        for sg, gd_list in zip(history_subgoals, subgoal_guidances):
            # normalize gd_list
            if gd_list is None:
                continue
            if not isinstance(gd_list, list):
                # Accept a single guidance string as a one-item list.
                gd_list = [gd_list]

            cleaned: List[str] = []
            for g in gd_list:
                if _is_null_like(g):
                    continue
                # Normalize guidance to stripped text.
                gs = str(g).strip()
                if gs and gs.lower() != "null":
                    cleaned.append(gs)

            if not cleaned:
                continue

            # Join the guidance fragments into one sentence.
            guidance_one_sentence = " ".join(cleaned).strip()
            if guidance_one_sentence:
                pairs.append({"subgoal": str(sg).strip(), "guidance": guidance_one_sentence})

        # all Null => do not store or use as context
        if not pairs:
            return repo_entries

        # find existing task entry
        idx = -1
        for i, e in enumerate(repo_entries):
            if isinstance(e, dict) and e.get("task") == task:
                idx = i
                break

        if idx == -1:
            repo_entries.append({"task": task, "subgoal_guidance": pairs})
        else:
            # merge: keep unique by subgoal text, latest wins
            old_list = repo_entries[idx].get("subgoal_guidance", [])
            merged: Dict[str, str] = {}

            if isinstance(old_list, list):
                for item in old_list:
                    if isinstance(item, dict):
                        s = str(item.get("subgoal", "")).strip()
                        g = str(item.get("guidance", "")).strip()
                        if s and g and g.lower() != "null":
                            merged[s] = g

            for item in pairs:
                s = item["subgoal"].strip()
                g = item["guidance"].strip()
                if s and g and g.lower() != "null":
                    merged[s] = g

            repo_entries[idx]["subgoal_guidance"] = [
                {"subgoal": s, "guidance": g} for s, g in merged.items()
            ]

        # persist JSONL
        self._dump_jsonl(repo_entries)
        return repo_entries

    # ------------------------------------------------
    # Retrieve the closest historical task descriptions.
    # ------------------------------------------------
    def retrieve_topk(
        self,
        task: str,
        k: int,
        repo_entries: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        RAG top-k by cosine similarity over bag-of-words vectors of `task` field.
        Returns the matched entry dicts (sorted by score desc).
        """
        if k <= 0:
            return []

        query_tokens = self._tokenize(task)
        if not query_tokens:
            return []

        q_vec = self._tf(query_tokens)

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in repo_entries:
            t = e.get("task", "")
            if not isinstance(t, str) or not t.strip():
                continue
            doc_tokens = self._tokenize(t)
            if not doc_tokens:
                continue
            d_vec = self._tf(doc_tokens)
            score = self._cosine(q_vec, d_vec)
            if score > 0:
                scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:k]]

    # -----------------------------------------
    # Format relevant experience for subgoal generation.
    # -----------------------------------------
    def build_prompt(self, matched_entries: List[Dict[str, Any]]) -> str:
        """
        Build a prompt snippet for SubgoalGenerator from matched memory entries.
        """
        if not matched_entries:
            return ""

        lines: List[str] = []
        lines.append("[SUBGOAL_MEMORY]")
        lines.append(
            "The following are historical cases where subgoal guidance was insufficient; use them to improve clarity and alignment."
        )
        lines.append(
            "Focus: make intermediate intent explicit when needed, and specify what must be memorized for later steps."
        )
        lines.append("----")

        for i, e in enumerate(matched_entries, start=1):
            lines.append(f"[CASE_{i}]")
            lines.append(f"Task: {e.get('task', '')}")
            sg_list = e.get("subgoal_guidance", [])
            if isinstance(sg_list, list) and sg_list:
                lines.append("SubgoalGuidance:")
                for j, item in enumerate(sg_list, start=1):
                    if not isinstance(item, dict):
                        continue
                    sg = str(item.get("subgoal", "")).strip()
                    gd = str(item.get("guidance", "")).strip()
                    if sg and gd:
                        lines.append(f"  {j}. Subgoal: {sg}")
                        lines.append(f"     Guidance: {gd}")
            lines.append("----")

        return "\n".join(lines).strip() + "\n"

    # =========================
    # internal helpers
    # =========================
    def _dump_jsonl(self, entries: List[Dict[str, Any]]) -> None:
        write_jsonl(self.repo_path, entries)

    def _tokenize(self, text: str) -> List[str]:
        # lowercase, keep alnum/underscore, split by non-word
        text = text.lower()
        toks = re.split(r"\W+", text)
        return [t for t in toks if t]

    def _tf(self, tokens: List[str]) -> Dict[str, float]:
        vec: Dict[str, float] = {}
        for t in tokens:
            vec[t] = vec.get(t, 0.0) + 1.0
        # normalize by length
        n = float(len(tokens)) if tokens else 1.0
        for k in list(vec.keys()):
            vec[k] /= n
        return vec

    def _cosine(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        # dot
        dot = 0.0
        for k, a in v1.items():
            b = v2.get(k)
            if b is not None:
                dot += a * b
        # norms
        n1 = math.sqrt(sum(a * a for a in v1.values()))
        n2 = math.sqrt(sum(b * b for b in v2.values()))
        if n1 == 0.0 or n2 == 0.0:
            return 0.0
        return dot / (n1 * n2)
