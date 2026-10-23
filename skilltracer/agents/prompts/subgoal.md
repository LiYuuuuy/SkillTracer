You choose the next subgoal for a browser task.

Use the current page, completed work and grounded memory. Choose one actionable
objective that advances the task. A subgoal is a semantic objective and may
require several supported actions. Do not repeat completed work, invent page
content, or navigate away from an answer already visible. Separate navigation,
form entry, submission and reading when later elements are not yet visible.
For information tasks, retain the needed evidence before leaving the page and
finish by sending the answer. For navigation or modification tasks, verify the
requested final state without inventing an additional reporting requirement.
When identifying information needed later, explicitly ask the downstream plan
to memorize the specific result before continuing.

Set terminate to true when completing this subgoal would finish the task.
If the task is already complete, return subgoal: None and terminate: true.
Evidence must be a verbatim AXTree slice with its original element IDs.

Task: $task
Completed subgoals: $history_subgoals
Completed plan: $history_plan
Completed actions: $history_actions
Relevant past experience: $subgoal_memory_text
Current AXTree:
$current_axtree

Return only:
subgoal: <one objective, or None>
terminate: <true or false>
evidence: <verbatim AXTree slice relevant to the objective>
