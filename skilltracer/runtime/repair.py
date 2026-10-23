"""Commit verified plan prefixes and replay a bounded rollback program."""

from skilltracer.agents.summarizer import InvalidAssessment
from skilltracer.memory.active import MemoryWrite
from skilltracer.memory.components import ComponentRegistry
from skilltracer.memory.experience import LongTermMemory, ShortTermMemory
from skilltracer.planning.actions import ActionRegistry
from skilltracer.planning.plan import Plan, concat_plans, slice_filled_plan_before_node

REPAIR_FIELDS = {
    "REPLACE_PURPOSE": (0, "new_purpose", "purpose"),
    "REPLACE_COMPONENT_TYPE": (1, "new_component_type", "component_type"),
    "REPLACE_ACTION": (2, "new_action", "action"),
    "REFINE_DESCRIPTION": (3, "new_description", "description"),
}


class RepairService:
    def __init__(self, browser, memory, rollback, artifacts=None):
        self.browser = browser
        self.memory = memory
        self.rollback = rollback
        self.artifacts = artifacts

    def commit(self, subgoal, current, batch, node_ids=None, *, preserve_edges=False):
        selected = set(batch.node_ids if node_ids is None else node_ids)
        if len(batch.node_ids) != len(set(batch.node_ids)) or len(batch.actions) != len(
            batch.node_ids
        ):
            raise ValueError("Committed actions must have unique, aligned node IDs.")
        if (
            len(batch.observations) != len(batch.actions) + 1
            or len(batch.browser_actions) != len(batch.browser_node_ids)
            or len(batch.browser_observations) != len(batch.browser_actions) + 1
        ):
            raise ValueError("Committed observations must be aligned with their actions.")
        existing = {node["node_id"] for node in subgoal.plan.get("nodes", [])}
        fresh = [
            node_id for node_id in batch.node_ids if node_id in selected and node_id not in existing
        ]
        if any(current.plan.get_node_by_id(node_id) is None for node_id in fresh):
            raise ValueError("Cannot commit an unknown plan node.")
        fresh_ids = set(fresh)
        if fresh:
            keep = set(fresh)
            boundary = subgoal.plan.get("nodes", [])[-1:]
            left_id = boundary[0]["node_id"] if boundary else None
            segment = Plan(
                {
                    "nodes": [current.plan.get_node_by_id(node_id) for node_id in fresh],
                    "edges": [
                        edge
                        for edge in current.plan.get("edges", [])
                        if edge.get("to") in keep
                        and (edge.get("from") in keep or edge.get("from") == left_id)
                    ],
                    "next_step_decision": {
                        "should_plan_next": current.plan.get("next_step_decision", {}).get(
                            "should_plan_next", True
                        )
                        if current.complete
                        else True
                    },
                }
            )
            if not current.plan.get("edges") and not current.plan.get("use_skill", False):
                segment.obj["edges"] = [
                    {"from": left, "to": right} for left, right in zip(fresh, fresh[1:])
                ]
            subgoal.plan = concat_plans(subgoal.plan, segment)
        subgoal.actions.extend(
            action for action, node_id in zip(batch.actions, batch.node_ids) if node_id in fresh_ids
        )
        subgoal.observations.extend(
            observation["axtree_object"]
            for observation, node_id in zip(batch.observations[1:], batch.node_ids)
            if node_id in fresh_ids
        )
        committed_browser = [
            (observation["url"], action)
            for observation, action, node_id in zip(
                batch.browser_observations, batch.browser_actions, batch.browser_node_ids
            )
            if node_id in fresh_ids
        ]
        self.rollback.update_from_success_trace(
            [url for url, _ in committed_browser],
            [action for _, action in committed_browser],
        )

    def apply(self, task, subgoal, current, batch, assessment):
        signal = assessment.repair_signal or {}
        if not isinstance(signal, dict):
            raise InvalidAssessment("Repair signal must be an object.")
        tool = signal.get("tool")
        if not isinstance(tool, str) or tool not in REPAIR_FIELDS:
            raise InvalidAssessment(f"Unsupported repair tool: {tool!r}")
        tool_id, parameter, field = REPAIR_FIELDS[tool]
        args = signal.get("arguments") or {}
        if not isinstance(args, dict):
            raise InvalidAssessment("Repair arguments must be an object.")
        node_id = assessment.error_node
        nodes = current.plan.get("nodes", [])
        node_ids = [node["node_id"] for node in nodes]
        if node_id not in node_ids or args.get("node_id") != node_id:
            raise InvalidAssessment(
                "Repair target and failed node must identify the same plan node."
            )
        if subgoal.plan.get_node_by_id(node_id) is not None:
            raise InvalidAssessment("A repair cannot change an already committed node.")
        value = args.get(parameter)
        if not isinstance(value, str) or not value.strip():
            raise InvalidAssessment(f"Repair requires a nonempty {parameter}.")
        if tool_id == 0 and value not in ComponentRegistry.PURPOSES:
            raise InvalidAssessment("Purpose repairs must use a registered semantic stage.")
        if tool_id == 2:
            try:
                ActionRegistry.primitive_name(value)
            except ValueError as exc:
                raise InvalidAssessment(str(exc)) from exc
        index = node_ids.index(node_id)
        allowed = set(node_ids[:index])
        existing = {node["node_id"] for node in subgoal.plan.get("nodes", [])}
        progressed = len(allowed.intersection(batch.node_ids) - existing)
        prefix = [
            action
            for action, node in zip(batch.browser_actions, batch.browser_node_ids)
            if node in allowed
        ]
        replay = self.rollback.build_rollback_actions(index, prefix)
        if self.artifacts is not None:
            self.artifacts.record(
                "repair_validated",
                assessment=assessment.to_dict(),
                rollback_actions=replay,
                source_plan=current.plan.to_dict(),
                newly_verified_prefix_count=progressed,
            )
        self.commit(subgoal, current, batch, allowed, preserve_edges=True)
        changes = batch.memory_changes
        if changes is None:
            changes = [(node, bid, None) for node, bid in batch.memory_pairs]
        for change in reversed(changes):
            if isinstance(change, MemoryWrite):
                memory_node, bid = change.node_id, change.bid
            else:
                memory_node, bid, previous = change
            if memory_node not in allowed:
                if isinstance(change, MemoryWrite):
                    self.memory.discard_record(change.memory_id)
                else:
                    self.memory.restore_memory_by_bid(bid, previous)
                restored_id = self.memory.latest_record_id(bid)
                if self.artifacts is not None:
                    self.artifacts.record(
                        "memory_restored" if restored_id is not None else "memory_deleted",
                        node_id=memory_node,
                        bid=bid,
                        memory_id=change.memory_id if isinstance(change, MemoryWrite) else None,
                        restored_memory_id=restored_id,
                    )
        failed_node = current.plan.get_node_by_id(node_id)
        previous_long_term = len(task.long_term)
        previous_repair = (
            subgoal.failures[-1].repair_tools[-1]
            if subgoal.failures and subgoal.failures[-1].repair_tools
            else {}
        )
        LongTermMemory.update_long_term_memory_on_error_transition(
            task.long_term,
            task=task.query,
            subgoal=subgoal.goal,
            short_term_memories=subgoal.failures,
            progressed_nodes=progressed,
            success_repair_tool=previous_repair.get("repair_tool"),
            success_repair_insight=previous_repair.get("insight"),
        )
        if self.artifacts is not None and len(task.long_term) > previous_long_term:
            self.artifacts.record(
                "long_term_memory_created",
                entries=[item.to_dict() for item in task.long_term[previous_long_term:]],
            )
        ShortTermMemory.update_short_term_memories_on_error(
            subgoal.failures,
            progressed_nodes=progressed,
            failed_node=failed_node,
            repair_tool=signal,
            insight=assessment.insight,
            error_evidence=assessment.error_evidence,
        )
        for action in replay:
            if self.artifacts is not None:
                self.artifacts.record("rollback_action_started", action=action)
            succeeded, _, _ = self.browser.execute_browsergym_action(action)
            if self.artifacts is not None:
                self.artifacts.record(
                    "rollback_action_finished", action=action, succeeded=succeeded
                )
            if not succeeded:
                raise RuntimeError(f"Rollback action failed: {action}")
        subgoal.past_plan = current.plan
        subgoal.last_plan_succeeded = False
        subgoal.planning_request = (
            f"Repair {node_id}: set {field} to {value!r} exactly. Preserve committed nodes. "
            + (
                "Regenerate the target node and replan every downstream node under the corrected "
                + ("interaction stage. " if tool_id == 0 else "UI grounding. ")
                if tool_id < 2
                else "Keep purpose, component grounding and all downstream nodes unchanged; no structural replanning. "
            )
            + f"Evidence: {assessment.error_evidence}. Advisory insight: {assessment.insight or ''}. "
            "The insight must not override the required patch or observed page state."
        )
        if tool_id > 1:
            current.plan.apply_repair_to_node(tool_id, node_id, value)
            current.filled = slice_filled_plan_before_node(current.filled or "", node_id)
            return True
        return False
