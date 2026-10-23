"""Bind and execute actions while retaining aligned page and memory traces."""

import json
import logging

from skilltracer.agents.parsing import extract_plan_components_block, get_delta_axtree
from skilltracer.memory.active import MemoryWrite
from skilltracer.planning.actions import ActionRegistry
from skilltracer.planning.checkpoints import CheckPointManager
from skilltracer.planning.conditions import check_incoming_edges
from skilltracer.planning.identity import resolve_node_id
from skilltracer.planning.plan import Plan

from .state import ExecutionBatch

logger = logging.getLogger(__name__)


class InvalidExecution(ValueError):
    """A model binding cannot be executed; provider and browser errors remain separate."""


class ExecutionService:
    def __init__(self, browser, agent, active_memory, accounts, artifacts):
        self.browser = browser
        self.agent = agent
        self.memory = active_memory
        self.accounts = accounts
        self.artifacts = artifacts

    def observe(self):
        page = self.browser.get_page_state()
        return {"url": page.get("url"), "axtree_object": page.get("axtree_object", "")}

    def execute(self, task, subgoal, current):
        before = self.observe()
        self.artifacts.snapshot(before["axtree_object"], "before_execution")
        evidence = current.evidence if current.rounds == 0 else before["axtree_object"]
        committed = {
            node["node_id"]: node
            for owner in (task, subgoal)
            for node in getattr(owner, "plan", Plan.init_empty()).get("nodes", [])
        }
        for node in current.plan.get("nodes", []):
            if node["node_id"] in committed and node != committed[node["node_id"]]:
                raise ValueError("Runnable plan changes an already committed node.")
        binding_plan = current.plan.to_executor_plan_obj()
        retained = [
            node["node_id"] for node in binding_plan["nodes"] if node["node_id"] in committed
        ]
        if retained:
            binding_plan["committed_node_ids"] = retained
        known_nodes = [node["node_id"] for node in current.plan.get("nodes", [])]
        completed = set(committed)
        edge_results = []

        def check_boundary(observation):
            target = next((node for node in known_nodes if node not in completed), None)
            if target is not None:
                results = check_incoming_edges(current.plan, target, completed, observation)
                edge_results.extend(results)
                for result in results:
                    self.artifacts.record("edge_validated", **result)

        check_boundary(before)
        task.steps += 1
        current.rounds += 1
        response = self.agent.execute(
            query=task.query,
            subgoal=subgoal.goal,
            axtree=evidence or before["axtree_object"],
            memory=self.memory.to_prompt(),
            account_information=self.accounts,
            plan_to_process=json.dumps(binding_plan, ensure_ascii=False, indent=2),
            filled_plan=current.filled,
        )
        _, context = extract_plan_components_block(response)
        actions, node_ids = ActionRegistry.executor_output_to_full_actions(response)
        resolved = [resolve_node_id(node, known_nodes) for node in node_ids]
        if any(node is None for node in resolved):
            raise InvalidExecution("Executor returned an action for an unknown plan node.")
        node_ids = resolved
        positions = [known_nodes.index(node) for node in node_ids]
        if positions != sorted(set(positions)):
            raise InvalidExecution("Executor actions must be unique and follow plan order.")
        skipped = [node for node in node_ids if node in committed]
        if skipped:
            self.artifacts.record("executor_prefix_skipped", node_ids=skipped)
        pending = [
            (action, node) for action, node in zip(actions, node_ids) if node not in committed
        ]
        actions = [action for action, _ in pending]
        node_ids = [node for _, node in pending]
        expected = [node for node in known_nodes if node not in committed]
        if node_ids != expected[: len(node_ids)]:
            raise InvalidExecution(
                "Executor actions must form the pending plan prefix; skipping the frontier is invalid."
            )
        for action, node in pending:
            try:
                actual = ActionRegistry.primitive_name(action)
                symbolic = ActionRegistry.primitive_name(
                    current.plan.get_node_by_id(node)["action"]
                )
            except ValueError as exc:
                raise InvalidExecution("Executor actions must be single primitive calls.") from exc
            if actual != symbolic:
                raise InvalidExecution("Executor changed the planned primitive action.")
        filled = "<plan components>:\n" + "\n".join(
            f"'''{node}: {action}'''" for action, node in pending
        )
        current.filled = filled
        observations = [before]
        browser_observations = [before]
        errors = []
        memory_changes = []
        # Preserve action order: a memorization after navigation must see the
        # resulting page rather than the page that existed before the batch.
        for action, node_id in zip(actions, node_ids):
            logger.info("Executing %s: %s", node_id, action)
            self.artifacts.record("action_started", action=action, node_id=node_id)
            if action.startswith("memorize("):
                bids = ActionRegistry.parse_memorize_bids([action])
                for bid in bids:
                    description = (current.plan.get_node_by_id(node_id) or {}).get(
                        "description", ""
                    )
                    content = self.memory.memorize(
                        bid,
                        observations[-1]["axtree_object"],
                        description,
                        source_url=observations[-1]["url"],
                    )
                    if content is not None:
                        memory_id = self.memory.latest_record_id(bid)
                        memory_changes.append(MemoryWrite(node_id, bid, memory_id))
                        self.artifacts.record(
                            "memory_written",
                            node_id=node_id,
                            bid=bid,
                            memory_id=memory_id,
                            content=content,
                        )
                observations.append(observations[-1])
            else:
                succeeded, _, _ = self.browser.execute_browsergym_action(action)
                after = self.observe()
                browser_observations.append(after)
                observations.append(after)
                if not succeeded:
                    errors.append(
                        self.browser.get_browsergym_obs().get("last_action_error", action)
                    )
            self.artifacts.record(
                "action_finished",
                action=action,
                node_id=node_id,
                observation=observations[-1],
                errors=list(errors),
            )
            if errors:
                break
            completed.add(node_id)
            check_boundary(observations[-1])
        count = len(observations) - 1
        if count < len(actions):
            actions, node_ids = actions[:count], node_ids[:count]
            filled = "<plan components>:\n" + "\n".join(
                f"'''{node}: {action}'''" for action, node in zip(actions, node_ids)
            )
            current.filled = filled
        browser_actions = [action for action in actions if not action.startswith("memorize(")]
        browser_nodes = [
            node for action, node in zip(actions, node_ids) if not action.startswith("memorize(")
        ]
        memory_pairs = [
            (node, bid)
            for action, node in zip(actions, node_ids)
            if action.startswith("memorize(")
            for bid in ActionRegistry.parse_memorize_bids([action])
        ]
        self.artifacts.snapshot(observations[-1]["axtree_object"], "after_execution")
        return ExecutionBatch(
            filled,
            context,
            actions,
            node_ids,
            browser_actions,
            browser_nodes,
            memory_pairs,
            observations,
            browser_observations,
            errors,
            memory_changes,
            edge_results,
        )

    @staticmethod
    def describe(batch, checkpoints):
        deltas = [
            get_delta_axtree(previous["axtree_object"], current["axtree_object"])
            for previous, current in zip(batch.observations, batch.observations[1:])
        ]
        initial = batch.observations[0]["axtree_object"]
        trajectory = checkpoints.build_delta_checkpoint_action_traj(
            initial,
            batch.actions,
            deltas,
            batch.node_ids,
        )
        text = CheckPointManager.traj_to_prompt(trajectory)
        if batch.edge_results:
            text += "\n[PROGRAM_EDGE_CHECKS]\n" + json.dumps(
                batch.edge_results, ensure_ascii=False, indent=2
            )
        return text
