"""Parse the text protocols shared by the agents."""

import json
import re
from typing import Optional, Tuple


def extract_plan_components_block(executor_text: str) -> Tuple[str, str]:
    """
    Return:
      - plan_components_block: raw <plan components> block (including header)
      - context_block: raw <context> block (including header)

    If a block is missing, return empty string for that part.
    """

    plan_block = ""
    context_block = ""

    # 1) extract <plan components> block
    m_plan = re.search(
        r"(<plan components>:\s*.*?)(?:\n<context>:|\Z)", executor_text, flags=re.DOTALL
    )
    if m_plan:
        plan_block = m_plan.group(1).strip()

    # 2) extract <context> block
    m_context = re.search(r"(<context>:\s*.*)", executor_text, flags=re.DOTALL)
    if m_context:
        context_block = m_context.group(1).strip()

    return plan_block, context_block


def get_delta_axtree(axtree1, axtree2) -> str:
    """
    Return lines newly present in the second accessibility tree.

    Args:
        axtree1: Accessibility tree before the action.
        axtree2: Accessibility tree after the action.

    Returns:
        delta_axtree: New accessibility-tree content in its original order.
    """

    if isinstance(axtree1, dict):
        axtree1 = json.dumps(axtree1, ensure_ascii=False, indent=2)
    if isinstance(axtree2, dict):
        axtree2 = json.dumps(axtree2, ensure_ascii=False, indent=2)
    lines1 = axtree1.splitlines()
    lines2 = axtree2.splitlines()

    # Keep lines absent from the previous observation.
    # Preserve the order of the new observation.
    delta_lines = [line for line in lines2 if line not in lines1]

    return "\n".join(delta_lines)


def split_plan_and_evidence(output: str) -> Tuple[Optional[str], Optional[str]]:
    lines = output.splitlines()
    n = len(lines)

    plan_text: Optional[str] = None
    evidence_text: Optional[str] = None

    # -------- 1) extract plan block (keep box) --------
    plan_start = None
    fence = None  # "```" or "'''"

    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("```plan"):
            plan_start, fence = i, "```"
            break
        if s.startswith("'''plan"):
            plan_start, fence = i, "'''"
            break

    plan_end = None
    if plan_start is not None:
        # closing fence is a line that equals fence exactly
        for j in range(plan_start + 1, n):
            if lines[j].strip() == fence:
                plan_end = j
                break
        if plan_end is not None:
            plan_text = "\n".join(lines[plan_start : plan_end + 1])
        else:
            plan_text = "\n".join(lines[plan_start:])  # best-effort
    else:
        # Some providers omit the plan fence but retain the evidence field.
        plan_text = _extract_first_json_object(output)

    # -------- 2) extract evidence (remove 'evidence:') --------
    search_from = (plan_end + 1) if plan_end is not None else 0

    i = search_from
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue

        if s.lower().startswith("evidence:"):
            after = raw.split(":", 1)[1]  # content after evidence:
            after_stripped = after.strip()

            # fenced evidence: evidence: ``` ... ```
            if after_stripped.startswith("```"):
                # same-line fenced
                if after_stripped.count("```") >= 2:
                    first = after_stripped.find("```") + 3
                    last = after_stripped.rfind("```")
                    evidence_text = after_stripped[first:last].strip()
                    return plan_text, evidence_text

                # multi-line fenced
                buf = []
                remainder = after_stripped[3:]
                if remainder.strip():
                    buf.append(remainder)

                i += 1
                while i < n and not lines[i].strip().startswith("```"):
                    buf.append(lines[i])
                    i += 1

                evidence_text = "\n".join(buf).strip("\n")
                return plan_text, evidence_text

            # raw multiline evidence slice (no fences)
            buf = []
            if after.strip() != "":
                buf.append(after.rstrip("\n"))

            i += 1
            while i < n:
                nxt = lines[i].strip()

                # Exclude tracebacks from the evidence slice.
                if nxt.startswith("Traceback (most recent call last):"):
                    break

                # stop at next top-level field (future-proof)
                if (
                    nxt.lower().startswith("evidence:")
                    or nxt.lower().startswith("subgoal:")
                    or nxt.startswith("```plan")
                    or nxt.startswith("'''plan")
                ):
                    break

                buf.append(lines[i])
                i += 1

            evidence_text = "\n".join(buf).strip("\n")
            return plan_text, evidence_text

        i += 1

    return plan_text, evidence_text


def _extract_first_json_object(text: str) -> str | None:
    """
    Extract the first balanced {...} object substring.
    Handles braces inside JSON strings.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    esc = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1].strip()

    return None
