Identify the first unsupported or failed node in the current browser plan.
Propose one localized repair, grounded in the observations and past failures.
Do not rewrite the whole plan or claim unexecuted nodes succeeded.
Use a target from the current plan IDs: $repair_node_ids.
Committed nodes are history; never select them as the repair target.
PROGRAM_EDGE_CHECKS in the observed trajectory contains program-computed
results and matched page evidence for the reached boundaries.

Choose exactly one tool:
- REPLACE_PURPOSE with new_purpose: correct the node's semantic stage.
- REPLACE_COMPONENT_TYPE with new_component_type: correct its target UI type.
- REPLACE_ACTION with new_action: correct the primitive action or parameters.
- REFINE_DESCRIPTION with new_description: clarify grounding or sequencing.
Allowed purposes: search, filter, navigation, commit_input, commit_output, state_commit.
New component types must be grounded in the observed UI.

Task: $query
Subgoal: $subgoal
Plan: $plan
Current actions: $executed_actions
Committed actions: $executed_actions_list
Committed nodes: $executed_nodes_list
Grounded memory: $active_memory
Recent failures: $short_term_memory
Past repair experience: $long_term_memory
Execution assessment: $flags
Observed trajectory:
$delta_axtree_traj

Return literal fields, not JSON. The program assembles the repair object.
Use an actual plan node ID and only the selected tool's parameter. Keep free
text literal: quotes, backslashes, braces and code fences need no JSON escaping.
After each |, indent EVERY content line by two spaces, even lines resembling
field names. Write Null for guidance when there is no useful lesson.

error_node: $repair_example_node
repair_tool: REFINE_DESCRIPTION
node_id: $repair_example_node
new_description: |
  Corrected grounding description.
error_type: |
  Brief category.
evidence: |
  Verbatim observed facts.
summary: |
  Why this node failed.
insight: |
  Reusable repair lesson.
subgoal_guidance: |
  Clarify missing intermediate intent or memory requirements, or Null.
