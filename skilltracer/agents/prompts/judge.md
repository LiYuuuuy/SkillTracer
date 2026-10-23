Assess only the browser evidence, not intended future actions. Compare current
execution with the plan and subgoal. Grounded memory is evidence equivalent to
the observed page. Do not treat an unexecuted node as a successful action.
PROGRAM_EDGE_CHECKS in the observed trajectory contains program-computed
checks on the current page. Use their results and matched evidence.

Task: $query
Subgoal: $subgoal
Plan: $plan
Current actions: $executed_actions
Previously committed actions: $executed_actions_list
Previously committed nodes: $executed_nodes_list
Grounded memory: $active_memory
Observed changes and actions:
$delta_axtree_traj

Return exactly six lines, each containing only true or false, in this order:
1. Have all nodes in the current plan been executed?
2. Did all actions in this round succeed?
3. Was this round empty, with no executed actions?
4. Does the observed change contain the information required by the task?
5. Is that information already present in executed actions or grounded memory?
6. Does the executed behavior advance the current subgoal?
