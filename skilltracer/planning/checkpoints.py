"""Plan checkpoints and browser rollback state."""

import json


class CheckPointManager:
    def __init__(self, plan):
        self.plan = plan
        self.nodes = plan["nodes"]
        self.node_ids = [node["node_id"] for node in self.nodes]
        self.checkpoints = [f"c{index}" for index in range(len(self.node_ids) + 1)]

    def build_delta_checkpoint_action_traj(
        self, axtree0_str, executed_actions, delta_axtree_traj, action_node_list
    ):
        if not len(executed_actions) == len(delta_axtree_traj) == len(action_node_list):
            raise ValueError("Actions, node IDs and page deltas must be aligned.")
        trajectory = [axtree0_str]
        for node, action, delta in zip(action_node_list, executed_actions, delta_axtree_traj):
            trajectory.extend([(node, action), delta])
        return trajectory

    @staticmethod
    def traj_to_prompt(trajectory):
        lines = []
        for index, item in enumerate(trajectory):
            if index % 2:
                node, action = item
                lines.extend([f"[NODE_{node} | ACTION_{index // 2}]", action])
            else:
                label = "[AXTREE_0]" if index == 0 else f"[ΔAXTREE_{index // 2}]"
                lines.extend(
                    [
                        label,
                        item
                        if isinstance(item, str)
                        else json.dumps(item, ensure_ascii=False, indent=2),
                    ]
                )
        return "\n".join(lines)

    def build_checkpoint_node_traj(self):
        trajectory = [self.checkpoints[0]]
        for index, node_id in enumerate(self.node_ids):
            trajectory.extend([node_id, self.checkpoints[index + 1]])
        return trajectory

    @staticmethod
    def checkpoint_node_traj_to_prompt(trajectory):
        return "-->".join(trajectory)


class RollBackPointer:
    def __init__(self, init_url=None):
        self.target_url = init_url
        self.action_buffer = []
        self.subgoal_url_snapshot = None
        self.subgoal_action_buffer_snapshot = []

    def update_from_success_trace(self, obs_url_traj, committed_actions):
        for url, action in zip(obs_url_traj, committed_actions):
            if url is not None and url != self.target_url:
                self.target_url = url
                self.action_buffer = []
            self.action_buffer.append(action)

    def snapshot_subgoal_start(self, current_url):
        self.subgoal_url_snapshot = current_url
        self.subgoal_action_buffer_snapshot = (
            list(self.action_buffer) if current_url == self.target_url else []
        )

    def build_rollback_actions(self, rollback_node_index, current_plan_executed_actions):
        actions = [f"goto({self.target_url!r})"] if self.target_url else []
        actions.extend(self.action_buffer)
        if rollback_node_index > 0:
            actions.extend(current_plan_executed_actions[:rollback_node_index])
        return actions

    def build_subgoal_rollback_actions(self):
        actions = (
            [f"goto({self.subgoal_url_snapshot!r})"]
            if self.subgoal_url_snapshot is not None
            else []
        )
        return actions + list(self.subgoal_action_buffer_snapshot)

    def reset_to_subgoal(self):
        self.target_url = self.subgoal_url_snapshot
        self.action_buffer = list(self.subgoal_action_buffer_snapshot)
