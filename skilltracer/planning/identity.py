"""Keep runnable node identities separate from the verified plan prefix."""

import copy
import re
from dataclasses import dataclass, field

from .plan import Plan


@dataclass
class PreparedPlan:
    plan: Plan
    renamed: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)


def prepare_plan(plan, previous):
    """Remove an unchanged prefix and repair colliding or nonmonotonic runnable IDs."""
    graph = plan.to_dict()
    old = {node["node_id"]: node for node in previous.get("nodes", [])}
    old_positions = {node_id: index for index, node_id in enumerate(old)}
    fresh, skipped = [], []
    last_old = -1
    for node in graph["nodes"]:
        node_id = node["node_id"]
        if node_id in old and node == old[node_id]:
            if fresh or old_positions[node_id] <= last_old:
                raise ValueError("Planner reordered the verified prefix.")
            skipped.append(node_id)
            last_old = old_positions[node_id]
        else:
            fresh.append(node)

    highest = max(
        (int(match.group(1)) for name in old if (match := re.fullmatch(r"n(\d+)", name))),
        default=0,
    )
    numbers = [
        int(match.group(1)) if (match := re.fullmatch(r"n(\d+)", node["node_id"])) else None
        for node in fresh
    ]
    needs_ids = any(number is None or number <= highest for number in numbers) or any(
        left >= right
        for left, right in zip(numbers, numbers[1:])
        if left is not None and right is not None
    )
    mapping = {
        node["node_id"]: f"n{highest + index + 1}" if needs_ids else node["node_id"]
        for index, node in enumerate(fresh)
    }
    new_ids = set(mapping)
    edges = graph.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("Graph edges must be a list.")
    retained_edges = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Graph edges must be objects.")
        source, target = edge.get("from"), edge.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("Graph edge endpoints must be string node IDs.")
        if source in new_ids and target in new_ids:
            retained_edges.append(
                {**copy.deepcopy(edge), "from": mapping[source], "to": mapping[target]}
            )
        elif source in old and target in old and source not in new_ids and target not in new_ids:
            if edge not in previous.get("edges", []):
                raise ValueError("Planner changed a verified prefix edge.")
        elif source in old and source not in new_ids and target in new_ids:
            if (
                source != next(reversed(old))
                or target != fresh[0]["node_id"]
                or set(edge) != {"from", "to"}
            ):
                raise ValueError(
                    "A prefix bridge must join the verified boundary without new conditions."
                )
        else:
            raise ValueError("Graph edge endpoints must refer to its nodes in execution order.")
    for node in fresh:
        node["node_id"] = mapping[node["node_id"]]
    positions = {node["node_id"]: index for index, node in enumerate(fresh)}
    if any(positions[edge["from"]] >= positions[edge["to"]] for edge in retained_edges):
        raise ValueError("Graph edges must follow execution order.")
    graph.update(nodes=fresh, edges=retained_edges)
    return PreparedPlan(
        Plan(graph),
        {old_id: new_id for old_id, new_id in mapping.items() if old_id != new_id},
        skipped,
    )


def resolve_node_id(value, candidates):
    """Resolve harmless formatting differences without assuming contiguous IDs."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value).strip().strip("`'\"").strip()
    if value in candidates:
        return value
    match = re.fullmatch(r"[nN]?(\d+)", value)
    if match and (canonical := "n" + str(int(match.group(1)))) in candidates:
        return canonical
    return None


def resolve_repair_target(error_node, argument_node, node_ids, committed=()):
    """Prefer valid current targets, otherwise fall back to the first uncommitted node."""
    pending = [node for node in node_ids if node not in committed]
    if not pending:
        raise ValueError("There is no uncommitted plan node to repair.")
    error = resolve_node_id(error_node, pending)
    argument = resolve_node_id(argument_node, pending)
    if error and argument and error != argument:
        raise ValueError("Repair target and failed node identify different current nodes.")
    target = error or argument or pending[0]
    resolution = None
    if error_node != target or argument_node != target:
        resolution = {
            "requested_error_node": error_node,
            "requested_argument_node": argument_node,
            "resolved_node": target,
            "reason": "current_node_reference" if error or argument else "first_uncommitted_node",
        }
    return target, resolution
