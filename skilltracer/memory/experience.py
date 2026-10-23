from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from skilltracer.storage import write_jsonl


class ShortTermMemory:
    """
    Short-term memory object for repeated correction failures within a subgoal.

    Variables (5):
      - original_failed_node: dict
      - repair_tools: List[Dict[str,str]]  # keys: repair_tool, insight
      - error_evidence: str
      - usable: bool
      - editable: bool  # default True
    """

    def __init__(
        self,
        original_failed_node: Dict[str, Any],
        repair_tool: Optional[str],
        insight: Optional[str],
        error_evidence: str,
        usable: bool = False,
        editable: bool = True,
    ):
        self.original_failed_node = copy.deepcopy(original_failed_node)
        self.repair_tools: List[Dict[str, str]] = []
        self.error_evidence = copy.deepcopy(error_evidence)
        self.usable = usable
        self.editable = editable
        self.add_repair_tool(repair_tool, insight)

    def add_repair_tool(self, repair_tool: str, insight: str) -> None:
        self.repair_tools.append(copy.deepcopy({"repair_tool": repair_tool, "insight": insight}))

    def set_usable(self, value: bool = True) -> None:
        self.usable = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_failed_node": self.original_failed_node,
            "repair_tools": self.repair_tools,
            "error_evidence": self.error_evidence,
            "usable": self.usable,
            "editable": self.editable,
        }

    def set_editable(self, value: bool) -> None:
        self.editable = value

    def update_failed_node(self, new_node: Dict[str, Any]) -> None:
        self.original_failed_node = copy.deepcopy(new_node)

    @staticmethod
    def to_prompt(memories: List[ShortTermMemory]) -> str:
        """
        Build prompt context text from short-term memories with usable=True.
        """
        chunks: List[str] = []
        for idx, m in enumerate(memories):
            if not m.usable:
                continue
            chunks.append(
                "\n".join(
                    [
                        f"[SHORT_TERM_MEMORY_{idx}]",
                        f"- original_failed_node: {m.original_failed_node}",
                        f"- error_evidence: {m.error_evidence}",
                        f"- repair_tools: {m.repair_tools}",
                    ]
                )
            )
        return "\n\n".join(chunks)

    @staticmethod
    def update_short_term_memories_on_error(
        memories: List[ShortTermMemory],
        *,
        progressed_nodes: int,
        failed_node: Dict[str, Any],
        repair_tool: Optional[str],
        insight: Optional[str],
        error_evidence: str,
    ) -> None:
        """
        Update short-term memory list after an execution attempt ends.

        Rules (exactly as specified):
        - If no error: do nothing.
        - If list empty: must create a new object, then stop.
        - If progressed_nodes == 0:
            - update last object's node (overwrite original_failed_node)
            - if last.editable is True:
                - update its repair tool
                - set usable=True
            - else (editable False):
                - create a new object, append to the end
        - If progressed_nodes >= 1:
            - set last.editable = False
            - create a new object and append
        """
        # Case 1: list empty -> create new object and exit
        if len(memories) == 0:
            m = ShortTermMemory(
                original_failed_node=failed_node,
                error_evidence=error_evidence,
                usable=False,
                editable=True,
                repair_tool=repair_tool,
                insight=insight,
            )
            memories.append(m)
            return  # Finish this update immediately.

        last = memories[-1]

        # Case 2: progressed_nodes == 0
        if progressed_nodes == 0:
            # Update the most recent failed node.
            last.update_failed_node(failed_node)

            if last.editable is True:
                # Record the repair and make this experience available as context.
                if repair_tool is not None and insight is not None:
                    last.add_repair_tool(repair_tool, insight)
                last.set_usable(True)
            else:
                # Append an editable entry when the previous one is frozen.
                m = ShortTermMemory(
                    original_failed_node=failed_node,
                    error_evidence=error_evidence,
                    usable=False,
                    editable=True,
                    repair_tool=repair_tool,
                    insight=insight,
                )
                memories.append(m)

            return

        # Case 3: progressed_nodes >= 1
        last.set_editable(False)
        m = ShortTermMemory(
            original_failed_node=failed_node,
            error_evidence=error_evidence,
            usable=False,
            editable=True,
            repair_tool=repair_tool,
            insight=insight,
        )
        memories.append(m)


class LongTermMemory:
    """
    Long-term memory item for summarizer: a successful correction trajectory
    that escaped a repeated failure point.

    Required attributes (in __init__):
      - task: str
      - subgoal: str
      - original_node: Dict[str, Any]
      - error_evidence: str
      - stuck_repair: List[Dict[str, str]]
      - success_repair: Dict[str, str]
    """

    def __init__(
        self,
        task: str,
        subgoal: str,
        original_node: Dict[str, Any],
        error_evidence: str,
        stuck_repair: List[Dict[str, str]],
        success_repair: Dict[str, str],
    ):
        self.task = task
        self.subgoal = subgoal
        self.original_node = copy.deepcopy(original_node)
        self.error_evidence = copy.deepcopy(error_evidence)
        self.stuck_repair = copy.deepcopy(stuck_repair)
        self.success_repair = copy.deepcopy(success_repair)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "subgoal": self.subgoal,
            "original_node": self.original_node,
            "error_evidence": self.error_evidence,
            "stuck_repair": self.stuck_repair,
            "success_repair": self.success_repair,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LongTermMemory":
        return LongTermMemory(
            task=str(d.get("task", "")),
            subgoal=str(d.get("subgoal", "")),
            original_node=d.get("original_node", {}) or {},
            error_evidence=d.get("error_evidence", "") or "",
            stuck_repair=d.get("stuck_repair", []) or [],
            success_repair=d.get("success_repair", {}) or {},
        )

    # -------------------------
    # Serialize the long-term experience list to JSONL.
    # -------------------------
    @staticmethod
    def import_long_term_memories_to_txt(
        memories: List[LongTermMemory],
        txt_path: str,
    ) -> None:
        """
        Write long-term memory list to txt as JSONL (one JSON per line).
        Overwrite the file only after a successful task.
        """
        write_jsonl(txt_path, (memory.to_dict() for memory in memories))

    # -------------------------
    # Load the long-term experience list from JSONL.
    # -------------------------
    @staticmethod
    def export_long_term_memories_from_txt(txt_path: str) -> List[LongTermMemory]:
        """
        Read JSONL from txt and return long-term memory list.
        If file missing/empty/bad lines, it will skip invalid lines.
        """
        out: List[LongTermMemory] = []
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if isinstance(d, dict):
                            out.append(LongTermMemory.from_dict(d))
                    except Exception:
                        # skip malformed line
                        continue
        except FileNotFoundError:
            return []
        return out

    # -------------------------
    # Promote experience when a new short-term repair record is created.
    # -------------------------
    @staticmethod
    def update_long_term_memory_on_error_transition(
        long_term_memories: List[LongTermMemory],
        *,
        task: str,
        subgoal: str,
        short_term_memories: List[Any],  # List[ShortTermMemory]
        progressed_nodes: int,
        success_repair_tool: Optional[str],
        success_repair_insight: Optional[str],
    ) -> None:
        """
        Update long-term memory list ONLY at the position that corresponds to:
        - In short-term update logic: progressed_nodes >= 1 branch
            (i.e., at least one node was progressed, and workflow will mark last STM editable=False
            and then create a new STM)

        This indicates the previous repair choice successfully escaped a repeated error point.

        Inputs:
        - short_term_memories: current STM list (subgoal-scoped)
        - progressed_nodes: number of plan nodes progressed in the latest execution attempt
        - success_repair_tool/insight: the repair (tool + insight) that led to escaping the stuck point

        Side-effect:
        - Append one LongTermMemory into long_term_memories when condition is met.
        """

        # Must have a previous short-term memory to summarize
        if not short_term_memories:
            return

        # Only trigger at the exact branch: progressed_nodes >= 1
        if progressed_nodes < 1:
            return

        # Need success repair info to store a meaningful snapshot
        if success_repair_tool is None or success_repair_insight is None:
            return

        last = short_term_memories[-1]

        original_node = getattr(last, "original_failed_node", {}) or {}
        error_evidence = getattr(last, "error_evidence", "") or ""
        stuck_repair = getattr(last, "repair_tools", []) or []

        long_term_memories.append(
            LongTermMemory(
                task=task,
                subgoal=subgoal,
                original_node=original_node,
                error_evidence=error_evidence,
                stuck_repair=stuck_repair,
                success_repair={
                    "repair_tool": success_repair_tool,
                    "insight": success_repair_insight,
                },
            )
        )

    # -------------------------
    # Format long-term repair experience for the summarizer.
    # -------------------------
    @staticmethod
    def build_long_term_memory_prompt(
        long_memories: List[LongTermMemory],
        *,
        max_items: int = 5,
    ) -> str:
        """
        Build a prompt-ready text for summarizer.
        Keep it short: most recent memories first.
        """
        if not long_memories:
            return ""

        # Use the most recent max_items entries.
        selected = long_memories[-max_items:]

        chunks: List[str] = []
        for i, m in enumerate(reversed(selected), start=1):
            chunks.append(
                "\n".join(
                    [
                        f"[LONG_TERM_MEMORY_{i}]",
                        f"- task: {m.task}",
                        f"- subgoal: {m.subgoal}",
                        f"- original_node: {m.original_node}",
                        f"- error_evidence: {m.error_evidence}",
                        f"- stuck_repair: {m.stuck_repair}",
                        f"- success_repair: {m.success_repair}",
                    ]
                )
            )

        return "\n\n".join(chunks)
