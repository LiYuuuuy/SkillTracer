"""Choose the next grounded objective and parse its evidence slice."""

from .base import Agent


class SubgoalGenerator(Agent):
    def __init__(self, llm):
        super().__init__(llm, "subgoal_generator")

    def generate(
        self,
        *,
        task,
        history_subgoals,
        history_plan,
        history_actions,
        subgoal_memory_text,
        current_axtree,
    ):
        output = self.ask(
            "subgoal",
            task=task,
            history_subgoals=history_subgoals,
            history_plan=history_plan,
            history_actions=history_actions,
            subgoal_memory_text=subgoal_memory_text,
            current_axtree=current_axtree,
        )

        def _parse_bool(s: str):
            v = (s or "").strip().lower()
            if v in {"true", "1", "yes", "y"}:
                return True
            if v in {"false", "0", "no", "n"}:
                return False
            return None

        subgoal = None
        evidence = None
        terminate = None

        lines = output.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            line = raw.strip()
            if not line:
                i += 1
                continue

            low = line.lower()

            # subgoal: ...
            if low.startswith("subgoal:"):
                subgoal = line.split(":", 1)[1].strip()
                i += 1
                continue

            # terminate: ...
            if low.startswith("terminate:"):
                val = line.split(":", 1)[1].strip()
                terminate = _parse_bool(val)
                i += 1
                continue

            # evidence: ...
            if low.startswith("evidence:"):
                after = raw.split(":", 1)[1]  # keep original spacing after colon
                after_stripped = after.strip()

                # Case 1: fenced evidence: ``` ... ``` (same-line or multi-line)
                if after_stripped.startswith("```"):
                    # Same-line fenced: evidence: ``` ... ```
                    if after_stripped.count("```") >= 2:
                        first = after_stripped.find("```") + 3
                        last = after_stripped.rfind("```")
                        evidence = after_stripped[first:last].strip()
                        i += 1
                        continue

                    # Multi-line fenced
                    block_lines = []
                    remainder = after_stripped[3:]  # after opening ```
                    if remainder.strip():
                        block_lines.append(remainder.rstrip("\n"))

                    i += 1
                    while i < len(lines):
                        if lines[i].strip().startswith("```"):
                            break
                        block_lines.append(lines[i].rstrip("\n"))
                        i += 1

                    evidence = "\n".join(block_lines).strip()
                    i += 1
                    continue

                # Case 2: raw multi-line slice WITHOUT fences
                block_lines = []
                # include the rest of the current line (might be empty)
                if after.strip() != "":
                    block_lines.append(after.rstrip("\n").strip("\r"))

                i += 1
                while i < len(lines):
                    nxt_raw = lines[i]
                    nxt = nxt_raw.strip().lower()
                    # stop if next top-level field begins
                    if (
                        nxt.startswith("subgoal:")
                        or nxt.startswith("evidence:")
                        or nxt.startswith("terminate:")
                    ):
                        break
                    block_lines.append(nxt_raw.rstrip("\n"))
                    i += 1

                evidence = "\n".join(block_lines).rstrip()
                continue

            i += 1

        return subgoal, evidence, terminate
