# Runtime behavior audit

Historical comparison: current [skill reuse and assessment policies](skill-reuse-and-assessment.md)
supersede the planner's first-round-only skill input and JSON repair protocol.
Legacy parity is not a default requirement for future changes.
Current [node recovery rules](node-identity-and-retrieval.md) also define
small-bank retrieval, repair-target fallback and aligned prefix commits.

The reference is the source snapshot saved before the package refactor. This
audit covers task selection, browser startup, state ownership, agent inputs,
action parsing, plan composition, assessment, rollback, learning and storage.
It compares valid execution protocols and separately identifies retained fixes
to defects in the original implementation. It does not claim identical model
outputs or a complete WebArena benchmark evaluation.

## Behavior comparison

| Area | Original behavior and current implementation |
| --- | --- |
| Task loading | Read intent, sites and all start URLs; preserve deep links while replacing configured site origins. The workflow uses the first site and URL, as before. |
| Task and plan limits | At most 11 subgoals and 21 planning rounds per subgoal. Executor attempts consume the step budget before the model call. Planning attempts are counted before the planner call. |
| Subgoal state | Start with an empty committed plan, action list, short-term memory and guidance. Working memory remains task-local across subgoals. |
| Planner inputs | The first plan uses the subgoal evidence slice and selected skill. Later plans use the current page and no selected skill. Committed subgoal state is separate from the task history, which advances at subgoal boundaries. |
| Executor inputs | A new plan starts with the planner evidence slice and no filled prefix. Later batches use the current page and the retained filled prefix. Projection exposes only node ID, component type, action and description, plus completion metadata. Edges now remain in the runtime; see [programmatic checks](runtime-edge-checks.md). |
| Assessment | The six flags retain the original repair branch: action failure or empty execution triggers repair. A direction mismatch alone does not. Nonconsecutive node IDs map to positional checkpoints. |
| Successful commit | Append verified nodes/actions/observations; update the rollback pointer with browser actions paired with their pre-action URLs. Memory actions never enter the browser replay buffer. |
| Rollback pointer | Keep the most recent pre-action URL and actions performed on that URL. A changed URL clears the buffer before appending its action. Build `goto(target)` + buffered actions + the current verified browser prefix. Subgoal snapshots are copies. |
| Repair boundary | Retain the verified prefix before the failed node. Remove working-memory entries associated with nodes at or after the failure. Update long-term experience before short-term experience, then replay the rollback program. |
| Purpose/type repair | Do not mutate the old plan in place. Send the exact field change to the planner and require regeneration of the target and all downstream nodes. |
| Action/description repair | Patch only that field in the current plan, truncate the filled prefix before the target and continue execution. Keep purpose, component grounding and downstream structure. |
| Memory references | `past_plan` references the current plan. The short-term failed-node record also references the node, so a local patch changes that record as in the original. Loaded banks remain runtime context and are not copied into attempt files. |
| Learning | Preserve the generation gates, task-start retrieval snapshots and separate type/skill/experience commit policies documented in [learning behavior](learning-behavior.md). |
| Final response | Join all literal `send_msg_to_user()` messages in their original order, stripping empty messages. |
| Evaluation | Information tasks require a sufficient agent response or an explicit unavailable response. Navigation/modification tasks are judged from actions and final state. Evaluation remains an LLM judgment. |
| Storage order | Save evaluation, then commit skills, long-term experience and subgoal experience in that order on success, then save trajectory. Components are committed earlier at eligible subgoal boundaries. No transaction spans the four banks. |
| Partial storage failure | Preserve completed writes in their original order, raise the storage error and retain task assets. Per-bank commit flags are recorded immediately after each successful commit routine. A flag denotes completion of the routine, not necessarily a changed entry. |
| Batch errors | Continue subsequent tasks, write the failing task's error and asset path, and return a failing process status if any runtime error occurred. |

Results may exist even when a later bank write fails: this matches the original
evaluation-before-learning write order. The attempt manifest and error record
identify that failure; trajectory writing has not occurred yet. Reusing an output directory skips existing result files
by default; use `--no-skip-existing` to rerun such a task.

## Differences corrected by this audit

- Executor projection now restores missing-field defaults and excludes arbitrary
  planner metadata, as the original projection did.
- Plan concatenation recognizes an existing conditional bridge by its endpoints;
  it no longer adds a second unconditional edge for the same bridge.
- Failed-prefix commits preserve original internal edge conditions. Already
  committed nodes remain deduplicated rather than being replayed into the plan.
- Repair requests again express the different downstream constraints of all
  four tools. Insights remain advisory and cannot override the required patch.
- Empty batches retain the last applicable checkpoint from committed progress.
- Line-based repair parsing accepts the original tool aliases, preserves evidence
  containing JSON text, filters argument fields and normalizes empty values.
- All user messages reach evaluation in order; the former last-message-only
  implementation dropped earlier answers.
- Attempt counts and filled bindings are updated before failures can interrupt
  execution. This preserves the original timing and improves error snapshots.
- Each bank commit is reported individually instead of marking every bank only
  after the entire group has returned. File reads and writes use UTF-8 explicitly.
- Skill selection again preserves the original parser's return rules, including
  inferring a match from an ID when `match` is absent or invalid.
- Trajectory writing follows successful bank routines, as in the original,
  rather than being performed together with the evaluation result.
- Only `InvalidAssessment` triggers the assessment-rejection replan. Provider,
  replay and storage `ValueError` exceptions propagate instead of being mistaken
  for malformed model output.

## Retained corrections to original defects

The following are deliberate differences and have regression coverage:

- Verified progress is retained when the final executor attempt exhausts the
  budget. The original learning block could omit that progress from evaluation.
- Memory actions observe the page at their actual position in the action order.
  The original captured every memory action before executing the browser batch.
- Null memory actions do not shift subsequent node-to-memory associations.
  Full committed action history retains memory actions even in a repair prefix.
- Repeated repairs do not duplicate already committed prefix nodes. The original
  could append the whole plan prefix again during later failures.
- Actual browser errors override a model success claim. Invalid repair targets
  are rejected before side effects, and a failed rollback replay stops execution.
  The original could continue after failed replay actions.
- Browser cleanup, per-attempt journals and isolated writable state remain part
  of the refactor. Original shared source files are not modified by experiments.
- Literal parsing decodes escaped answer strings safely; it does not execute
  action text. Arbitrary URLs are quoted safely in replay programs.
- Filled bindings are truncated strictly before a failed node. The original
  helper's double-escaped regex failed to recognize ordinary node lines.
- Verified task progress is aggregated before skill induction, so an induction
  exception cannot hide the completed subgoal from the retained task state.

The prompts state each agent's responsibilities concisely. The directives above
preserve their operational constraints; sampled model choices can still lead to
different subgoals, plans or learned assets.

## Executable references and validation

`tests/fixtures/runtime_reference.json` contains outputs obtained by executing
the original Python implementations, with hashes of their source files:

- 30 rollback state/program operations across three initial states;
- executor projection, copied plan slices, conditional bridges and empty-plan metadata;
- three checkpoint/action/observation trajectories with nonconsecutive node IDs;
- 17 JSON and line-based repair responses;
- all 64 combinations of the six assessment flags;
- ordered response aggregation and mixed browser/memory action parsing.

The separate learning fixture retains the 36 learning-gate combinations, six
memory transitions and three subgoal-memory updates from the earlier audit.
Integration tests cover all four repair tools with interleaved memory actions,
URL changes, repeated failures, agent input scopes, partial bank-write failures
and batch continuation. Tests consume these fixtures without requiring the
historical source backup.

## Removed accounting

The obsolete token estimation module, per-role counters, agent parameters,
runtime dependencies and result/progress usage fields have been removed.
Agent role names now belong to the model configuration module. New experiments
save evaluation, trajectories and learning assets without usage estimates.
The provider generation-length setting remains available through
`--max-output-tokens`; it controls output length rather than measuring usage.
Historical results and the source backup are preserved for comparison.

## Domain asset policy

Following this control-flow audit, asset storage was changed at the user's
request: new domains start empty, all reusable banks and output paths are scoped
by task domain, and attempts record only produced or modified entries. The
package no longer supplies historical learned banks. Ordinary model journals
omit full prompts to avoid serializing loaded knowledge on every call. Debug
mode explicitly opts into full prompts. Provenance records committed production
without recording initialization or consumption of old assets.

These are deliberate storage-policy changes; the learning gates, repair memory
transitions, retrieval guard and commit order above remain unchanged. Existing
flat state is archived with hashes, and only task-attributed production is
recovered into scoped banks. See [asset management](asset-management.md).

A live cold-start run also exposed a valid JSON plan returned without its
requested code fence, followed by the separate evidence field. The parser now
extracts that balanced JSON object without consuming the evidence. This accepts
the same plan schema and leaves fenced-plan behavior unchanged; a regression
covers quoted braces and missing plans.

The subsequent [state and call audit](state-trace-audit.md) replays 12 archived
workflows and checks 88 normalized calls with their state at each call boundary.
It covers aliases, function return contracts and storage fault boundaries in
addition to the isolated references described above.
