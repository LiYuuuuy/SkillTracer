"""Match edge predicates against the current accessibility-tree observation."""

import ast
import re
import unicodedata

_QUOTED = r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
_NODE = re.compile(r"^\s*(?:\[(?P<bid>[^\]]+)\]\s*)?(?P<role>[A-Za-z][\w-]*)(?:\s+(?P<body>.*))?$")
_FIELD = re.compile(r"\b(\w+)\s*=\s*(" + _QUOTED + r"|[+-]?\d+(?:\.\d+)?|True|False|None)")


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _nodes(axtree):
    for line in axtree.split("\n"):
        match = _NODE.match(line)
        if not match:
            continue
        body = match.group("body") or ""
        fields = []
        name = re.match(_QUOTED, body)
        if name:
            fields.append(("name", ast.literal_eval(name.group())))
            body = body[name.end() :]
        for field in _FIELD.finditer(body):
            if field.group(1) in {"name", "value", "label", "text", "description"}:
                fields.append((field.group(1), str(ast.literal_eval(field.group(2)))))
        yield {"bid": match.group("bid"), "role": match.group("role"), "fields": fields}


def _predicate(expression):
    parsed = ast.parse(expression, mode="eval").body
    names = {"exists_component": "component_type", "exists_content": "target_content"}
    if (
        not isinstance(parsed, ast.Call)
        or not isinstance(parsed.func, ast.Name)
        or parsed.func.id not in names
    ):
        raise ValueError("Only exists_component and exists_content are supported.")
    if len(parsed.args) == 1 and not parsed.keywords:
        argument = parsed.args[0]
    elif (
        not parsed.args
        and len(parsed.keywords) == 1
        and parsed.keywords[0].arg == names[parsed.func.id]
    ):
        argument = parsed.keywords[0].value
    else:
        raise ValueError("A condition requires one literal string argument.")
    if (
        not isinstance(argument, ast.Constant)
        or not isinstance(argument.value, str)
        or not _normalized(argument.value)
    ):
        raise ValueError("A condition requires one nonempty literal string argument.")
    return parsed.func.id, _normalized(argument.value)


def check_condition(expression, axtree):
    """Return a boolean and observed evidence; never execute predicate expressions."""
    result = {"condition": expression, "result": False, "evidence": []}
    try:
        function, target = _predicate(expression)
        for node in _nodes(axtree):
            if function == "exists_component":
                if _normalized(node["role"]) == target:
                    result.update(
                        result=True, evidence=[{"bid": node["bid"], "role": node["role"]}]
                    )
                    break
            else:
                for field, value in node["fields"]:
                    if target in _normalized(value):
                        result.update(
                            result=True,
                            evidence=[{"bid": node["bid"], "field": field, "value": value}],
                        )
                        break
                if result["result"]:
                    break
    except (SyntaxError, TypeError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def check_incoming_edges(plan, target, completed, observation):
    """Check the reached boundary using its current page, including across batches."""
    results = []
    for edge in plan.get("edges", []):
        if edge.get("to") != target or edge.get("from") not in completed:
            continue
        expressions = edge.get("preconditions", edge.get("precondition", []))
        if not isinstance(expressions, list):
            expressions = [expressions]
        for expression in expressions:
            results.append(
                {
                    "from": edge["from"],
                    "to": target,
                    "url": observation.get("url"),
                    **check_condition(expression, observation.get("axtree_object", "")),
                }
            )
    return results
