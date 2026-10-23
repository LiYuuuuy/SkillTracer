Abstract one reusable skill from a successfully executed subgoal plan.

Retain the demonstrated actions and their order. Generalize task entities,
literal values and element IDs into meaningful parameter names. Keep primitive
action names unchanged. Do not invent steps or broaden the subgoal.
Keep purpose, component_type, action and description on every node.
Number nodes n1, n2, ... and connect them in order. Add a precondition only
when the observed page demonstrates that it is necessary.
Refine the existing skill with its original ID only if its scope and procedure
match; otherwise create a new unique skill ID. Produce exactly one skill.

Task: $query
Completed subgoal: $subgoal
Successfully executed plan: $executed_plan
Previously selected skill: $reused_skill

Return only:
'''skill
{"id":"skill_unique_name","name":"verb-oriented name","description":"task-independent goal","plan":{"nodes":[{"node_id":"n1","purpose":"navigation","component_type":"link","action":"click(target_link_id)","description":"Open the target link."}],"edges":[],"next_step_decision":{"should_plan_next":false}}}
'''
