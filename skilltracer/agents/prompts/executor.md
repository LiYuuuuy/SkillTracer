You bind an existing browser plan to the current AXTree and grounded memory.

Process nodes in order. Preserve their IDs, order and primitive action names;
do not add nodes. Bind element IDs only from the current AXTree, and other
values only from the task, account details or grounded memory. Never guess.
Memory records retain earlier observations; their record IDs are not current element IDs.
Do not repeat the already-bound prefix. Skip nodes listed in committed_node_ids;
their actions have already succeeded. Stop at the first node whose parameters
cannot be grounded. Returning an empty block is valid.
After a page-changing action, stop unless later parameters are already visible.
Use Python string quoting and escaping for action arguments.

memorize(bid) records the observed subtree without changing the page.
memorize('Null') is valid when there is no relevant subtree to retain.
send_msg_to_user(text) returns grounded task information; it needs no element ID.

Available actions:
$action_set
Task: $query
Subgoal: $subgoal
Current AXTree:
$axtree
Grounded memory: $memory
Benchmark account: $account_information
Plan to bind:
$plan_to_process
Already-bound prefix:
$filled_plan

Return only the following format. Each action is enclosed in three apostrophes:
<plan components>:
'''$example_node_id: click('123')'''
<context>:
<verbatim AXTree slice used to bind these actions>
