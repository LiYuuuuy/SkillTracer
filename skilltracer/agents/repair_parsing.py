"""Assemble repair objects from literal fields; accept valid legacy JSON too."""

import json
import re
from textwrap import dedent

from .parsing import _extract_first_json_object

PARAMETERS = {
    "new_purpose": "REPLACE_PURPOSE",
    "new_component_type": "REPLACE_COMPONENT_TYPE",
    "new_action": "REPLACE_ACTION",
    "new_description": "REFINE_DESCRIPTION",
}
FIELDS = {
    "error_node",
    "error_type",
    "repair_tool",
    "repair_signal",
    "node_id",
    "evidence",
    "summary",
    "insight",
    "subgoal_guidance",
    *PARAMETERS,
}
SECTIONS = {"arguments", "error_evidence"}


def _header(line):
    match = re.fullmatch(r"([a-z_]+)\s*(?::(.*))?", line.strip(), re.I)
    if match and match.group(1).lower() in FIELDS | SECTIONS:
        key, value = match.groups()
        key = key.lower()
        if value is not None or key in SECTIONS:
            return key, value.strip() if value is not None else ""
    return None


def _unwrap(text):
    lines = dedent(text.lstrip("\ufeff").replace("\r\n", "\n")).strip().split("\n")
    if lines:
        opening = re.fullmatch(r"(```|''')(?:summary|text|yaml|json)?\s*", lines[0])
        if opening and lines[-1].strip() == opening.group(1):
            lines = lines[1:-1]
    return "\n".join(lines)


def _literal_fields(text):
    lines, result = text.split("\n"), {}
    index = 0
    while index < len(lines):
        header = _header(lines[index])
        if header is None:
            index += 1
            continue
        key, value = header
        if key in SECTIONS:
            index += 1
            continue
        if key in result:
            raise ValueError(f"Duplicate repair field: {key}.")
        indentation = len(lines[index]) - len(lines[index].lstrip())
        block = value in {"|", "|-", "|+", ">", ">-", ">+"}
        content = [] if block else [value]
        index += 1
        while index < len(lines):
            line = lines[index]
            depth = len(line) - len(line.lstrip())
            # Indented block text stays literal even if it looks like another field.
            if _header(line) is not None and (not block or depth <= indentation):
                break
            content.append(line)
            index += 1
        value = dedent("\n".join(content)).strip("\n") if block else "\n".join(content).strip()
        result[key] = value or None
    if not result:
        raise ValueError("No repair fields were found.")
    arguments = {key: result[key] for key in ("node_id", *PARAMETERS) if key in result}
    data = {
        key: value
        for key, value in result.items()
        if key not in {*PARAMETERS, "node_id", "evidence", "summary"}
    }
    data["arguments"] = arguments
    data["error_evidence"] = {key: result.get(key) for key in ("evidence", "summary")}
    return data


def parse_repair(text):
    """Keep the (repair dict, guidance string) interface without text-to-JSON interpolation."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Repair output must contain text.")
    body = _unwrap(text)
    # Prefer the field protocol before looking for JSON in observed evidence.
    field_protocol = any(_header(line) is not None for line in body.split("\n"))
    if field_protocol and not body.lstrip().startswith("{"):
        data = _literal_fields(body)
    else:
        block = _extract_first_json_object(body)
        try:
            data = json.loads(block) if block is not None else None
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Malformed JSON repair; use literal fields with indented text blocks."
            ) from exc
        if not isinstance(data, dict):
            raise ValueError("Repair output is neither a repair object nor literal repair fields.")
    signal = data.get("repair_signal") or data.get("repair_tool") or {}
    if isinstance(signal, str):
        signal = {"tool": signal, "arguments": data.get("arguments", {})}
    if not isinstance(signal, dict):
        raise ValueError("Repair signal must be an object.")
    arguments = signal.get("arguments", data.get("arguments")) or {}
    if not isinstance(arguments, dict):
        raise ValueError("Repair arguments must be an object.")
    tool = signal.get("tool") or next(
        (name for key, name in PARAMETERS.items() if key in arguments), None
    )
    data["repair_signal"] = {"tool": tool, "arguments": arguments}
    data["error_node"] = data.get("error_node") or arguments.get("node_id")
    data.setdefault("error_type", None)
    data.setdefault("insight", None)
    evidence = data.get("error_evidence")
    data["error_evidence"] = (
        evidence if isinstance(evidence, dict) else {"evidence": evidence, "summary": None}
    )
    guidance = data.get("subgoal_guidance")
    return data, str(guidance) if guidance is not None and str(guidance).strip().lower() not in {
        "",
        "null",
    } else "Null"
