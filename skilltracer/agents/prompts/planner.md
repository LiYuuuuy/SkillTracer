You plan browser actions for the current subgoal.

Use observed evidence and abstract parameters. Preserve primitive action names.
Memory records retain earlier observations; their record IDs are not current element IDs.
Do not repeat committed nodes; continue node numbering after history.
Use memorize(result_id) for intermediate information needed later.

$reuse_instructions

Every node has node_id, purpose, component_type, action and description.
Purposes: search, filter, navigation, commit_input, commit_output, state_commit.
The registry recommends types; new observed types are allowed.
Edges connect nodes in execution order.
Set should_plan_next=false when the plan completes this subgoal, otherwise true.
An empty nodes list is valid when no further action is needed.

Available actions:
$action_set
Component registry:
$type_set

Task: $query
Subgoal: $subgoal
Current AXTree:
$axtree
Previous plan: $past_plan
Previous plan succeeded: $plan_status
Committed subgoal plan: $executed_plan
Task history: $history_plan
Task actions: $history_actions
Grounded memory: $active_memory
Planning request: $task_description

Return this exact format, with valid JSON and a verbatim evidence slice:
```plan
{$reuse_fields"nodes":[{"node_id":"n1","purpose":"navigation","component_type":"link","action":"click(target_id)","description":"Open the target link."}],"edges":[],"next_step_decision":{"should_plan_next":false}}
```
evidence: <verbatim AXTree slice supporting this plan>
