# Retrieval thresholds and node identity recovery

## Retrieval with a small bank

Any nonempty domain skill bank is eligible for retrieval. `top_k=3` limits the
candidate count; it is not a minimum bank size. An empty bank still skips the
reuser. Existing candidate ranking and the reuser's match decision remain active.
A retrieval miss still removes all skill instructions and example fields from
the planner prompt. Skill induction keeps its separate two-node and budget gates.

## Legacy recovery rules retained

The original `util/util.py::node_id_to_plan_index` located IDs by plan order,
accepted `node_id` or `id`, and returned index zero when lookup failed.
`agent/summarizer.py::checkpoint_before_error_node` also returned `c0` on a
missing reference. Checkpoints represent positions, not the numeric suffix of an ID.

The current implementation retains that recovery intent while respecting the
verified prefix. Plans accept the legacy `id` alias and store canonical
`node_id` values. Repair targets use the actual ordered current-plan IDs,
including gaps such as `n3`, `n8`, `n20`.

Target resolution proceeds as follows:

1. Resolve the reported error node and tool argument against uncommitted nodes.
   Harmless quoting, numeric references and `N008` can resolve to an existing `n8`.
2. Prefer a valid current reference. If both references are valid but disagree,
   reject the repair instead of choosing between conflicting targets.
3. If neither reference is valid, select the first uncommitted node and align
   both fields to it. With no verified prefix this gives the legacy `c0` behavior.
   After committed `n3`, a fallback to pending `n8` gives `c1`.
4. Reject repair if no uncommitted node exists. Verified history is never a
   fallback repair target.

The fallback is a conservative restart position, not proof that attribution was
semantically correct. Repair and executor prompt examples use current pending
IDs rather than a fixed `n1`. The summarizer still has its original two stages
and the same call signature and `Assessment` fields. A fallback is recorded as
`repair_node_resolved`, preserving requested IDs and the effective target.

## Ordinary plans and collision recovery

Before execution, the ordinary `use_skill=false` path removes an unchanged
cumulative prefix. If new nodes restart their IDs, collide with verified history,
use nonnumeric IDs, or reverse numeric order, they receive consecutive `nN` IDs
after the largest committed numeric ID. All internal edge endpoints are remapped;
actions, descriptions, literal strings and edge conditions are unchanged.
Valid increasing IDs with gaps are retained. Duplicate IDs inside a proposed
graph are ambiguous and remain invalid, as do dangling and backward edges.

For example, committed `n1: click(...)` followed by a new `n1: hover(...)` becomes
`n2: hover(...)`. The old `n1` stays committed. The skill-reuse path retains its
exact subgraph validation and inclusive `skill_start_node_id` fallback policy;
that field always refers to an original skill ID, not a runtime-renumbered ID.

`plan_node_ids_normalized` records the remapping and any omitted verified prefix.

## Executor and commit safeguards

The binding graph supplies `committed_node_ids` when part of the current plan
has already succeeded. A repeated executor prefix is filtered before browser
or working-memory operations; the effective batch and assessment input contain
only the remaining actions. Ambiguous unknown executor nodes are still rejected.
A current plan that changes an already committed node is blocked before model
binding or browser execution.

Commit applies the same fresh-node set to plan nodes, actions, observations and
the rollback buffer. Recommitting a batch cannot append its actions a second
time. This closes the observed case where the plan had two nodes but its
committed action trace contained three actions. Successful prefix conditions
and existing rollback replay semantics remain intact.

These changes leave final evaluation and successful-task bank commit gates
unchanged. They do not address category-level success misclassification.
