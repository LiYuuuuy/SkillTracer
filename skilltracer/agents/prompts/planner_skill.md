A matching skill is available:
$skill

Output use_skill as a JSON boolean. If false, plan normally and set
skill_start_node_id=null. If true, inspect the current page and committed
progress to identify which original skill node the current state is immediately
BEFORE. Output its original node_id as skill_start_node_id, before renumbering.
That node has NOT executed yet: include it as the first new node. Omit earlier
skill nodes. Never report the last completed node.

Copy a nonempty induced subgraph starting at that node. Preserve node content,
order, internal edges and conditions exactly. Do not adapt actions, descriptions,
purposes or types. Only node IDs and edge endpoints may be renumbered.
On mismatch, the program appends the skill suffix INCLUDING the reported node.
An invalid entry ID falls back to the full skill.
