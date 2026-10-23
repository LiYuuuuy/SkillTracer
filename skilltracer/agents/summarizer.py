"""Assess execution and request a localized repair when required."""

import re
from dataclasses import asdict, dataclass, field

from skilltracer.planning.identity import resolve_repair_target
from skilltracer.planning.plan import Plan

from .base import Agent
from .repair_parsing import parse_repair

FLAG_NAMES = (
    "all_plan_nodes_executed",
    "all_actions_executed_success",
    "empty_execution",
    "target_info_in_delta_axtree",
    "target_info_in_actions_or_memory",
    "direction_matches_subgoal",
)


class InvalidAssessment(ValueError):
    """Model output cannot be applied; no execution or storage failure is implied."""


def _parse_flags(response):
    if not isinstance(response, str):
        raise InvalidAssessment("Execution judge returned no text.")
    values, named, numbered = [], {}, {}
    for raw in response.splitlines():
        line = raw.strip()
        field = re.fullmatch(r"([a-z_]+)\s*:\s*(true|false)", line, re.I)
        if field and field.group(1).lower() in FLAG_NAMES:
            key = field.group(1).lower()
            if key in named:
                raise InvalidAssessment(f"Duplicate execution flag: {key}.")
            named[key] = field.group(2).lower() == "true"
            continue
        number = re.fullmatch(r"Q?([1-6])[.):=]\s*(true|false)", line, re.I)
        if number:
            index = int(number.group(1))
            if index in numbered:
                raise InvalidAssessment(f"Duplicate execution flag number: {index}.")
            numbered[index] = number.group(2).lower() == "true"
            continue
        value = re.fullmatch(r"(true|false)", line, re.I)
        if value:
            values.append(value.group(1).lower() == "true")
    if named:
        if len(named) != len(FLAG_NAMES):
            raise InvalidAssessment("Execution judge must provide all six named flags.")
        return {key: named[key] for key in FLAG_NAMES}
    if numbered:
        if len(numbered) != len(FLAG_NAMES):
            raise InvalidAssessment("Execution judge must provide all six numbered flags.")
        return {name: numbered[index] for index, name in enumerate(FLAG_NAMES, 1)}
    if len(values) != len(FLAG_NAMES):
        raise InvalidAssessment("Execution judge must return exactly six boolean lines.")
    return dict(zip(FLAG_NAMES, values))


@dataclass
class Assessment:
    plan_success: bool
    checkpoint: str
    error_node: str | None = None
    error_type: str | None = None
    error_evidence: dict | None = None
    repair_signal: dict | None = None
    insight: str | None = None
    guidance: str = "Null"
    flags: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Summarizer(Agent):
    def __init__(self, llm):
        super().__init__(llm, "summarizer")

    def summarize(
        self,
        *,
        query,
        subgoal,
        plan,
        delta_axtree_traj,
        executed_actions,
        executed_actions_list,
        executed_nodes_list,
        active_memory,
        short_term_memory,
        long_term_memory,
        checkpoints,
        last_checkpoint,
        action_errors=(),
    ):
        self.last_node_resolution = None
        context = dict(
            query=query,
            subgoal=subgoal,
            plan=plan,
            delta_axtree_traj=delta_axtree_traj,
            executed_actions=executed_actions,
            executed_actions_list=executed_actions_list,
            executed_nodes_list=executed_nodes_list,
            active_memory=active_memory,
        )
        response = self.ask("judge", **context)
        flags = _parse_flags(response)
        if action_errors:
            flags["all_actions_executed_success"] = False
        # Preserve the original policy: action failure or an empty execution
        # requires repair; partial, successful execution may continue.
        needs_repair = not flags["all_actions_executed_success"] or flags["empty_execution"]
        if not needs_repair:
            return Assessment(True, last_checkpoint, flags=flags)

        try:
            committed = (
                [node["node_id"] for node in Plan.from_symbolic(executed_nodes_list).get("nodes")]
                if executed_nodes_list and executed_nodes_list not in {"None", "null"}
                else []
            )
        except ValueError as exc:
            raise InvalidAssessment(str(exc)) from exc
        pending = [node for node in checkpoints.node_ids if node not in committed]
        if not pending:
            raise InvalidAssessment("There is no uncommitted plan node to repair.")
        response = self.ask(
            "repair",
            **context,
            short_term_memory=short_term_memory,
            long_term_memory=long_term_memory,
            flags={**flags, "action_errors": list(action_errors)},
            repair_node_ids=pending,
            repair_example_node=pending[0],
        )
        try:
            repair, guidance = parse_repair(response)
        except ValueError as exc:
            raise InvalidAssessment(str(exc)) from exc
        signal = repair.get("repair_signal")
        if not isinstance(signal, dict) or not signal.get("tool"):
            raise InvalidAssessment("Repair response has no supported tool.")
        arguments = signal.get("arguments")
        if not isinstance(arguments, dict):
            raise InvalidAssessment("Repair arguments must be an object.")
        try:
            node, self.last_node_resolution = resolve_repair_target(
                repair.get("error_node"), arguments.get("node_id"), checkpoints.node_ids, committed
            )
        except ValueError as exc:
            raise InvalidAssessment(str(exc)) from exc
        arguments["node_id"] = node
        return Assessment(
            False,
            checkpoints.checkpoints[checkpoints.node_ids.index(node)],
            error_node=node,
            error_type=repair.get("error_type"),
            error_evidence=repair.get("error_evidence"),
            repair_signal=signal,
            insight=repair.get("insight"),
            guidance=guidance,
            flags=flags,
        )
