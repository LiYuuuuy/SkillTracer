"""Enforce exact, ordered skill subgraphs before any planner graph is executed."""

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, field

from .identity import prepare_plan
from .plan import InvalidPlan, Plan, concat_plans, parse_plan_payload


@dataclass
class ReuseDecision:
    plan: Plan
    requested: bool
    replaced: bool = False
    reason: str = "not_requested"
    skill_id: str | None = None
    node_mapping: dict = field(default_factory=dict)
    skill_start_node_id: str | None = None
    planner_node_mapping: dict = field(default_factory=dict)
    skipped_prefix_node_ids: list = field(default_factory=list)


def _validate_graph(graph):
    graph = Plan.from_symbolic(json.dumps(graph)).to_dict()
    nodes, edges = graph["nodes"], graph["edges"]
    if not isinstance(edges, list):
        raise ValueError("Graph edges must be a list.")
    positions = {node["node_id"]: index for index, node in enumerate(nodes)}
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in positions
            or edge.get("to") not in positions
        ):
            raise ValueError("Graph edge endpoints must refer to its nodes.")
        if positions[edge["from"]] >= positions[edge["to"]]:
            raise ValueError("Graph edges must follow execution order.")
    return graph


def _content(node):
    return {key: value for key, value in node.items() if key != "node_id"}


def _edges(graph, source, target):
    return Counter(
        json.dumps(
            {key: value for key, value in edge.items() if key not in {"from", "to"}}, sort_keys=True
        )
        for edge in graph.get("edges", [])
        if edge["from"] == source and edge["to"] == target
    )


def _subgraph_mapping(delta, skill, *, anchor_start=False):
    """Find an order-preserving induced match, including every internal edge condition."""
    nodes, originals = delta.get("nodes", []), skill["nodes"]
    if not nodes or len(nodes) > len(originals):
        return None
    candidates = [
        [index for index, original in enumerate(originals) if _content(node) == _content(original)]
        for node in nodes
    ]

    def search(chosen):
        if len(chosen) == len(nodes):
            return {
                node["node_id"]: originals[index]["node_id"] for node, index in zip(nodes, chosen)
            }
        for index in candidates[len(chosen)]:
            if anchor_start and not chosen and index != 0:
                continue
            if chosen and index <= chosen[-1]:
                continue
            if len(originals) - index < len(nodes) - len(chosen):
                continue
            if any(
                _edges(delta, nodes[previous]["node_id"], nodes[len(chosen)]["node_id"])
                != _edges(skill, originals[prior]["node_id"], originals[index]["node_id"])
                for previous, prior in enumerate(chosen)
            ):
                continue
            result = search(chosen + [index])
            if result is not None:
                return result
        return None

    return search([])


def _delta(proposed, previous):
    """Allow suffix or cumulative planner output while preserving the committed prefix."""
    old = {node["node_id"]: node for node in previous.get("nodes", [])}
    nodes = proposed["nodes"]
    new_nodes = [node for node in nodes if node["node_id"] not in old]
    new_ids = {node["node_id"] for node in new_nodes}
    seen_new = False
    old_positions = {node_id: index for index, node_id in enumerate(old)}
    last_old = -1
    for node in nodes:
        if node["node_id"] in old:
            if (
                seen_new
                or old[node["node_id"]] != node
                or old_positions[node["node_id"]] <= last_old
            ):
                raise ValueError("Planner changed or reordered the committed prefix.")
            last_old = old_positions[node["node_id"]]
        else:
            seen_new = True
    for edge in proposed.get("edges", []):
        source, target = edge["from"], edge["to"]
        if target in old:
            if source not in old or edge not in previous.get("edges", []):
                raise ValueError("Planner changed an edge in the committed prefix.")
        elif source in old and (source != next(reversed(old)) or target != new_nodes[0]["node_id"]):
            raise ValueError("A composition bridge must join the prefix boundary to the new graph.")
        elif source in old and set(edge) != {"from", "to"}:
            raise ValueError("A composition bridge cannot introduce new edge conditions.")
    return Plan(
        {
            **copy.deepcopy(proposed),
            "nodes": copy.deepcopy(new_nodes),
            "edges": [
                copy.deepcopy(edge)
                for edge in proposed.get("edges", [])
                if edge["from"] in new_ids and edge["to"] in new_ids
            ],
        }
    )


def _skill_suffix(skill, start_node_id):
    """Cut immediately before the original entry node, retaining its full remaining graph."""
    index = next(i for i, node in enumerate(skill["nodes"]) if node["node_id"] == start_node_id)
    graph = copy.deepcopy(skill)
    graph["nodes"] = graph["nodes"][index:]
    retained = {node["node_id"] for node in graph["nodes"]}
    graph["edges"] = [
        edge for edge in graph["edges"] if edge["from"] in retained and edge["to"] in retained
    ]
    return graph


def _append_skill(previous, skill, start_node_id):
    existing = {node["node_id"] for node in previous.get("nodes", [])}
    highest = max(
        (int(match.group(1)) for name in existing if (match := re.fullmatch(r"n(\d+)", name))),
        default=0,
    )
    graph = _skill_suffix(skill, start_node_id)
    mapping = {}
    for node in graph["nodes"]:
        highest += 1
        mapping[node["node_id"]] = f"n{highest}"
        node["node_id"] = f"n{highest}"
    for edge in graph.get("edges", []):
        edge["from"], edge["to"] = mapping[edge["from"]], mapping[edge["to"]]
    graph["use_skill"] = True
    graph["skill_start_node_id"] = start_node_id
    combined = concat_plans(previous, Plan(graph))
    combined.obj["use_skill"] = True
    combined.obj["skill_start_node_id"] = start_node_id
    # The runtime already owns the verified prefix; only the appended graph is runnable.
    return _delta(combined.to_dict(), previous), {
        new: original for original, new in mapping.items()
    }


def manage_skill_plan(text, previous, selected_skill):
    """Keep planner I/O textual; return the executable graph and a compact decision."""
    proposed = parse_plan_payload(text)
    requested = proposed.get("use_skill", False)
    if not isinstance(requested, bool):
        raise InvalidPlan("Planner use_skill must be a boolean.")
    if not requested:
        try:
            prepared = prepare_plan(Plan.from_symbolic(json.dumps(proposed)), previous)
        except (ValueError, TypeError) as exc:
            raise InvalidPlan(str(exc)) from exc
        return ReuseDecision(
            prepared.plan,
            False,
            planner_node_mapping=prepared.renamed,
            skipped_prefix_node_ids=prepared.skipped,
        )
    selected = (
        json.loads(selected_skill)
        if isinstance(selected_skill, str) and selected_skill not in {"", "None", "null"}
        else selected_skill
    )
    if not isinstance(selected, dict) or not isinstance(selected.get("plan"), dict):
        raise InvalidPlan("Planner requested skill reuse without an injected skill.")
    try:
        skill = _validate_graph(selected["plan"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Injected skill graph is invalid: {exc}") from exc
    if not skill["nodes"]:
        raise ValueError("An injected skill must have at least one node.")
    start_node_id = proposed.get("skill_start_node_id")
    valid_start = isinstance(start_node_id, str) and any(
        node["node_id"] == start_node_id for node in skill["nodes"]
    )
    try:
        if "skill_start_node_id" in proposed and not valid_start:
            raise ValueError("Planner skill_start_node_id must identify an original skill node.")
        # Suffix-only edges may refer to the already retained prefix boundary.
        known = {node["node_id"] for node in previous.get("nodes", [])}
        with_prefix = {
            **proposed,
            "nodes": previous.to_dict()["nodes"]
            + [node for node in proposed["nodes"] if node["node_id"] not in known],
        }
        _validate_graph(with_prefix)
        delta = _delta(proposed, previous)
        if any(re.fullmatch(r"n\d+", node["node_id"]) is None for node in delta.get("nodes", [])):
            raise ValueError("Runnable planner node IDs must use n followed by digits.")
        remaining = _skill_suffix(skill, start_node_id) if valid_start else skill
        mapping = _subgraph_mapping(delta.obj, remaining, anchor_start=valid_start)
        if mapping is not None:
            return ReuseDecision(
                delta,
                True,
                reason="skill_subgraph",
                skill_id=selected.get("id"),
                node_mapping=mapping,
                skill_start_node_id=next(iter(mapping.values())),
            )
        reason = "not_a_skill_subgraph"
    except (ValueError, KeyError, TypeError) as exc:
        reason = str(exc)
    entry = start_node_id if valid_start else skill["nodes"][0]["node_id"]
    replacement, mapping = _append_skill(previous, skill, entry)
    return ReuseDecision(replacement, True, True, reason, selected.get("id"), mapping, entry)
