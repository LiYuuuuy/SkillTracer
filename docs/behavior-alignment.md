# End-to-end behavior and data ownership

This is the current contract. Historical parity fixtures remain useful for
unchanged call boundaries, but they do not override the corrections below.
Final task evaluation and successful-task bank commit gates are unchanged.

## Agent and runtime boundaries

| Boundary | Authoritative input | Enforced result |
| --- | --- | --- |
| Subgoal generation | Task query, completed task plan/actions, current browser tree and retrieved subgoal guidance | One next objective; terminal status controls continuation |
| Skill reuse | Up to three retrieved skills from the current domain's loaded bank | One stored skill is enough for retrieval; selected IDs must belong to the presented candidates |
| Planning | Subgoal evidence on the first round, current tree later; verified task and subgoal history; selected skill on every round | Typed completion fields, unique runnable IDs, ordinary collision recovery, exact declared skill subgraphs and inclusive skill-entry fallback |
| Binding | Node projection without edges, committed-node IDs, observed page, working memory and account context | A contiguous prefix of pending nodes; preserve primitive names; no unknown nodes or multiple-call payloads |
| Execution | Validated bindings in plan order | Stop at the first failed browser action; compute edge results at reached boundaries for assessment |
| Assessment | Actual initial browser observation, action/page deltas and programmatic edge results | Existing six-flag judge, followed by literal repair fields when required; browser errors override a success claim |
| Repair | Current uncommitted node, typed tool parameter, verified prefix and actual batch | Valid node fallback, consistent checkpoint position and bounded replay; purpose changes must use a registered stage |
| Commit | The same fresh verified node set | Aligned plan nodes, bound actions, observations and rollback buffer; preserve authored edge conditions |
| Learning | Verified subgoal plan and current budgets | Existing skill/type generation gates; only produced entities enter attempt records |
| Persistence | Domain-scoped candidates and final task evaluation | Component commits at eligible boundaries; skill and experience commits on final success |

Invalid model plan syntax/structure raises `InvalidPlan`, which permits another
planning round within the existing 21-round subgoal bound. Invalid binding
structure raises `InvalidExecution`, which permits replanning within the
executor budget. `InvalidAssessment` remains the assessment/repair retry path.
Provider, browser, replay and storage exceptions are not converted into these
model-output exceptions. Raw responses are recorded before interpretation.
Replacement actions are validated as supported single primitive calls before
any rollback or memory update. Malformed stored skills remain asset errors,
not retryable planner output.

An executor response can repeat an already committed prefix, which is filtered
before any effect. It cannot skip an unfinished node, change click to hover,
or execute later nodes after a failed action. Effective batch arrays contain
only attempted nodes, including the failed node. The intended binding remains
available in the raw model-call journal. Ordinary plans retain explicit internal
edge conditions across partial executor rounds; an ordinary plan without edges
uses the existing sequential composition edges.

Edge results are appended to the existing assessment trajectory, without an
additional execution gate or retry policy. See [programmatic edge checks](runtime-edge-checks.md)
for the two string-matching rules and their timing.

## Variable lifetimes

| State | Lifetime and propagation |
| --- | --- |
| `TaskState.plan/actions` | Verified history from completed or exhausted subgoal processing; passed to subsequent planning and evaluation |
| `SubgoalState.plan/actions` | Verified progress inside the current subgoal; checkpoint and prefix recovery use these same IDs |
| `PlanState.plan` | The managed runnable graph; a local action/description repair changes this graph; `past_plan` intentionally references it |
| `PlanState.filled` | Current effective binding; truncated strictly before a locally repaired node |
| Working memory | Task-local observation records with source URLs; older captures survive reused browser IDs; visible immediately to planning, binding and assessment |
| Short-term memory | Subgoal-local failure snapshots; the first record is not yet prompt-usable; repeated failure without progress can make it usable |
| Long-term memory | Produced at a later error transition after new verified progress; prompt context remains the task-start snapshot |
| Skill candidates | Staged separately from the task-start retrieval bank; current-task induction does not mutate loaded skills |
| Component registry | Updated at eligible subgoal boundaries; planner recommendations remain the workflow-start snapshot |
| Subgoal guidance | Final planning round's non-Null guidance for completed subgoals; earlier guidance remains in events |

The two-node skill-induction minimum, executor-budget gate, 20-round learning
gate, final-round guidance rule and task-start retrieval snapshots remain active.
They are intentional generation/visibility rules, not missing assignments.

## Rollback and repair experience

Rollback computes a replay program from the previously verified anchor plus
the newly verified prefix of the failed batch. Prefix commit and replay share
the same node order, including nonconsecutive IDs and memory-only nodes.
Already committed actions are not appended to the rollback buffer again.

Working-memory undo records only actual writes in the current batch, using
stable record IDs. Undo runs in reverse write order for the failed suffix and
removes those exact records; older pages and verified-prefix records survive.
Older batches carrying previous text retain a compatibility recovery path.
No initialized memory-bank snapshot is recorded. See
[input and memory behavior](input-memory-response.md) for the record format.

Repair progress is the number of **newly verified** nodes before the failure,
excluding nodes committed in an earlier executor round. Repeated failure at
the same pending node cannot promote long-term memory merely because its
absolute plan index is nonzero. When new progress escapes a previous failure,
the promoted entry records the previous repair that enabled that progress,
not the new repair proposed for the next failure.

Failure nodes, repair histories and promoted experience are copied at creation.
Later action/description patches cannot rewrite an earlier failure snapshot.
Structured long-term evidence preserves its JSON type when reloaded.
Successful assessment alone still does not trigger long-term promotion.

## Storage and practical limits

Skill-bank reading decodes the complete JSON object before finding its closing
fence, so literal apostrophe fences, quotes, braces and backslashes in evidence
round-trip correctly. Long-term and subgoal JSONL banks are replaced only after
successful serialization; an encoding failure leaves the previous bank intact.
Bank provenance points to produced attempt assets rather than copies of loaded
banks. Defaults and earlier experiment banks are not changed by validation.

Replay is navigation plus browser actions, not a transaction over arbitrary
external side effects. The four bank writes are ordered but do not form one
cross-bank transaction. Independent workers need separate state roots; shared
concurrent bank merging is not implemented. Assessment still relies on model
interpretation of observations and addition-only tree deltas. These checks do
not guarantee correct skill choice, DOM grounding or task completion by a model.

Tests cover skipped nodes, primitive changes, failure truncation, programmatic
edge evidence, callback recovery, immutable repair experience, progress-sensitive
promotion, memory undo, conditional-edge commits and serialization failure.
Real-browser probes exercise both natural model decisions and controlled
failure/rollback paths; their outcomes are recorded separately.
