"""Symbolic plan serialization, composition and localized repair."""

import copy
import json
import re
from dataclasses import dataclass

from skilltracer.memory.components import ComponentRegistry


class InvalidPlan(ValueError):
    """Model plan syntax or structure is invalid; execution has not started."""


def parse_plan_payload(text):
    """Read a JSON object without confusing quoted code fences with framing."""
    if not isinstance(text, str):
        raise InvalidPlan("Planner returned no plan text.")
    opening = re.search(r"(?m)^[ \t]*(?:```|''')plan(?:[ \t]*\r?\n|[ \t]*(?=\{))", text)
    body = (text[opening.end() :] if opening else text).lstrip()
    try:
        obj, end = json.JSONDecoder().raw_decode(body)
    except ValueError as exc:
        raise InvalidPlan(f"Invalid plan JSON: {exc}") from exc
    remainder = body[end:].strip()
    if not opening and remainder:
        raise InvalidPlan("Unexpected text after the plan object.")
    if opening and not remainder.startswith(("```", "'''")):
        raise InvalidPlan("Planner plan block is missing its closing fence.")
    if not isinstance(obj, dict):
        raise InvalidPlan("A plan must be an object.")
    return obj


@dataclass
class Plan:
    obj: dict

    @staticmethod
    def init_empty(should_plan_next=True):
        return Plan(
            {"nodes": [], "edges": [], "next_step_decision": {"should_plan_next": should_plan_next}}
        )

    @staticmethod
    def from_symbolic(text):
        obj = parse_plan_payload(text)
        if not isinstance(obj, dict) or not isinstance(obj.get("nodes"), list):
            raise InvalidPlan("A plan must contain a nodes list.")
        ids = []
        for node in obj["nodes"]:
            if isinstance(node, dict) and "node_id" not in node and "id" in node:
                node["node_id"] = node.pop("id")
            if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
                raise InvalidPlan("Every plan node requires a string node_id.")
            if not isinstance(node.get("action"), str):
                raise InvalidPlan("Every plan node requires an action.")
            if "purpose" in node and node["purpose"] not in ComponentRegistry.PURPOSES:
                raise InvalidPlan("Plan node purpose must use a registered semantic stage.")
            ids.append(node["node_id"])
        if len(set(ids)) != len(ids):
            raise InvalidPlan("Plan node IDs must be unique.")
        obj.setdefault("edges", [])
        obj.setdefault("next_step_decision", {"should_plan_next": True})
        decision = obj["next_step_decision"]
        if not isinstance(decision, dict) or not isinstance(decision.get("should_plan_next"), bool):
            raise InvalidPlan("next_step_decision.should_plan_next must be a boolean.")
        return Plan(obj)

    def get(self, key, default=None):
        return self.obj.get(key, default)

    def to_dict(self):
        return copy.deepcopy(self.obj)

    def to_symbolic(self):
        return "```plan\n" + json.dumps(self.obj, ensure_ascii=False, indent=2) + "\n```"

    def get_node_by_id(self, node_id):
        return next((node for node in self.obj["nodes"] if node["node_id"] == node_id), None)

    def to_executor_plan_symbolic(self):
        return json.dumps(self.to_executor_plan_obj(), ensure_ascii=False, indent=2)

    def to_executor_plan_obj(self):
        return {
            "nodes": [
                {
                    "node_id": node.get("node_id"),
                    **{
                        key: node.get(key, "")
                        for key in ("component_type", "action", "description")
                    },
                }
                for node in self.obj.get("nodes", [])
            ],
            "next_step_decision": copy.deepcopy(
                self.obj.get("next_step_decision", {"should_plan_next": True})
            ),
        }

    def slice_before_node(self, node_id):
        nodes = self.obj.get("nodes", [])
        index = next((i for i, node in enumerate(nodes) if node["node_id"] == node_id), None)
        if index is None:
            raise ValueError(f"Unknown plan node: {node_id}")

        def segment(selected):
            ids = {node["node_id"] for node in selected}
            return Plan(
                {
                    "nodes": copy.deepcopy(selected),
                    "edges": [
                        copy.deepcopy(edge)
                        for edge in self.obj.get("edges", [])
                        if edge.get("from") in ids and edge.get("to") in ids
                    ],
                    "next_step_decision": {"should_plan_next": True},
                }
            )

        return segment(nodes[:index]), segment(nodes[index:])

    def apply_repair_to_node(self, tool_id, target_node_id, parameter):
        fields = {2: "action", 3: "description"}
        if tool_id not in fields or not isinstance(parameter, str) or not parameter.strip():
            raise ValueError(
                "A local repair must update an action or description with nonempty text."
            )
        node = self.get_node_by_id(target_node_id)
        if node is None:
            raise ValueError(f"Unknown repair target: {target_node_id}")
        node[fields[tool_id]] = parameter

    @staticmethod
    def concat_plan_node(executed_plan, plan_obj, node, *, plan_complete=False):
        if node is None:
            return executed_plan
        segment = Plan(
            {
                "nodes": [copy.deepcopy(node)],
                "edges": [],
                "next_step_decision": {
                    "should_plan_next": plan_obj.get("next_step_decision", {}).get(
                        "should_plan_next", True
                    )
                    if plan_complete
                    else True
                },
            }
        )
        return concat_plans(executed_plan, segment)


def concat_plans(first, second):
    left, right = first.to_dict(), second.to_dict()
    left_nodes, right_nodes = left.get("nodes", []), right.get("nodes", [])
    edges = left.get("edges", []) + right.get("edges", [])
    if left_nodes and right_nodes:
        bridge = {"from": left_nodes[-1]["node_id"], "to": right_nodes[0]["node_id"]}
        if not any(
            edge.get("from") == bridge["from"] and edge.get("to") == bridge["to"] for edge in edges
        ):
            edges.append(bridge)
    return Plan(
        {
            "nodes": left_nodes + right_nodes,
            "edges": edges,
            "next_step_decision": right.get(
                "next_step_decision", left.get("next_step_decision", {"should_plan_next": True})
            ),
        }
    )


def slice_filled_plan_before_node(filled_plan, error_node_id):
    """Retain the bound prefix strictly before the failed node."""
    pattern = re.compile(r"'''\s*" + re.escape(error_node_id) + r"\s*:")
    kept = []
    for line in filled_plan.splitlines():
        if pattern.search(line) or line.strip().startswith("<context>"):
            break
        kept.append(line)
    return "\n".join(kept)
