# State and function-call audit

This is a historical baseline audit. Current [skill reuse and assessment policies](skill-reuse-and-assessment.md)
supersede its planner skill-visibility and repair-output descriptions. New work
need not emulate the archived behavior unless the user explicitly requests it.
Current [node recovery rules](node-identity-and-retrieval.md) also supersede
the former invalid-target rejection and ordinary node-identity behavior.
The [current alignment contract](behavior-alignment.md) supersedes live failure
aliases, absolute-index progress, and model-selected initial observation slices.

This audit executes the archived `Workflow.run` method with deterministic agent
outputs and browser observations. Its plan, action, checkpoint, memory, skill
and repair helpers come from the same archived source. Browser/model services,
the workspace root and diagnostic output are replaced to keep the reference
offline and prevent changes to historical assets.

`tests/fixtures/workflow_reference.json` contains source hashes, synthetic inputs,
88 normalized calls and their state for 12 complete workflows. Tests consume
this fixture without requiring the historical backup. The fixture is synthetic
reference data; ordinary attempts continue to record produced assets only.

## Variable ownership and mutation

| Original variable | Current owner | Assignment and visibility contract |
| --- | --- | --- |
| `current_step` | `TaskState.steps` | Increment before the executor model call; failures still consume the attempt. |
| `stuck_saver` | `SubgoalState.planning_rounds` | Reset per subgoal; increment before planning; at most 21 rounds; learning requires at most 20. |
| `all_skill` | `TaskState.skills` | Load once per task from its domain; selection continues to use this bank throughout the task. |
| `all_skill_temp` | `TaskState.candidate_skills` | Distinct shallow-copy list, initially sharing skill objects; upsert replaces or appends without mutating the retrieval list. |
| `long_term_memory` | `TaskState.long_term` | Append at the original error transition; bank only after successful final evaluation. |
| `long_term_memory_copy` | `TaskState.long_term_context` | Freeze the most recent five loaded entries as prompt context; new promotions do not enter current-task repair context. |
| `subgoal_memory_inject` | `TaskState.subgoal_context` | Retrieve at task start from the current domain only. |
| `executed_plan` / `executed_actions` | `SubgoalState.plan` / `.actions` | Retain verified progress for this subgoal; a repair retains its successful prefix. |
| `final_executed_plans` / `final_executed_actions` | `TaskState.plan` / `.actions` | Aggregate at subgoal boundaries; later planning sees preceding subgoals separately from current progress. |
| `past_plan` | `SubgoalState.past_plan` | Reference the current plan after assessment; local repairs remain visible through this alias. |
| `plan_status` | `SubgoalState.last_plan_succeeded` | True at subgoal start and after success; false after repair or rejected assessment. |
| `filled_plan` | `PlanState.filled` | Reset for each new plan; assign the current binding before browser execution; truncate before a locally repaired node. |
| `plan_completion` | `PlanState.complete` | Empty plan or assessment at the last positional checkpoint. |
| `subgoal_complete` | `SubgoalState.complete` | Complete plan with `should_plan_next=false`; only completed subgoals enter completed history. |
| `subgoal_guidance_temp` | `SubgoalState.guidances` | Reset for each new planning round; only its final round contributes to reusable completed-subgoal guidance. |
| `short_term_memories` | `SubgoalState.failures` | Reset per subgoal; first failure is unusable, repeated no-progress repair can make it usable. |
| `failed_node` | Failure memory's node reference | Shares the current plan node; an action/description patch changes this node in the failure record too. |
| `Task_end_flag` | `TaskState.finished` | Terminal subgoal must complete; reaching the execution budget alone does not finish the task. |

Plan slices deep-copy nodes and edges. The runtime's `Plan.to_dict()` also
returns a copy for observations and producer files; the original returned its
live dictionary. Operational aliases use the plan object or `get_node_by_id()`,
so this serialization change does not remove the live aliases above.

## Function and return contracts

| Call | Return and caller behavior |
| --- | --- |
| Subgoal generation | `(goal, evidence, terminal)`; first plan receives its evidence slice and selected skill. Later plans receive current observations and no selected skill. |
| Planning | Symbolic plan text plus evidence; parse and validate before executing; reset filled bindings per plan. |
| Execution | Parse browser and memory actions with aligned node IDs; preserve order; retain paired pre/post observations; increment step count before the model request. |
| Browser action | `(success, terminated, truncated)`; reported action errors override an assessment claiming success. Thrown browser exceptions propagate. |
| Assessment | `Assessment` represents the original serialized summary; checkpoint positions follow full-plan node order, including nonconsecutive IDs. Action failure or empty execution enters repair. |
| Repair | Return true for action/description patch and continued execution; return false for purpose/type repair and replanning. Validate the target and argument before side effects. |
| Skill selection | Preserve `(matched, id)` rules: absent/invalid `match` falls back to ID presence; false clears the ID; a true match can still have no usable ID, which lookup rejects. |
| Skill induction | Parse the returned skill and upsert by ID; unknown IDs become the next `sN`. Retrieval uses loaded skills, not current candidates. |
| Evaluation | `(evaluation, prompt)`; save its result, commit successful-task banks in order, then save trajectory. Failed evaluation skips those bank writes but still saves its trajectory. |

## Ordering and exceptional paths

An eligible subgoal commits discovered types before final evaluation. Final
storage follows the archived order:

```text
evaluation -> result file -> skills -> long-term memory -> subgoal experience -> trajectory
```

The three bank routines run only on success. No transaction spans their writes.
Fault tests inject an exception at each component/result/bank/trajectory write,
check that subsequent calls do not occur, and verify the completed bank flags.
If trajectory writing fails, successful bank commits remain successful; the
attempt status is error and execution history remains in the attempt files.

Only `InvalidAssessment` means that a model's assessment cannot be applied and
the planner may retry. A provider request, rollback action or asset writer can
also raise `ValueError`; these are real task errors and must propagate. They
cannot be relabeled as assessment rejection and cause another planning call.
Regression cases exercise each of these three exception sources.

## Corrected reproduction differences

This pass corrected three unintended refactor differences:

1. Skill selection had discarded the original fallback and some original tuple
   return values. Twelve original parser cases now compare directly.
2. Trajectory writing had moved before bank commits. It now follows them,
   including the original behavior when a bank write raises.
3. A broad `except ValueError` had swallowed provider, replay and storage errors.
   Dedicated assessment validation now controls the retry boundary.

The following deliberate corrections are not treated as exact legacy parity:

- The original filled-prefix helper double-escaped whitespace in its regex and
  failed to drop the bad node. Tests apply the helper's stated strict-prefix
  contract to the corresponding expected binding field; all other call fields
  still compare to the actual archived output.
- Task-level verified progress is retained before learning and at exhausted
  budgets. An induction exception or budget limit cannot hide executed progress.
  At an induction call, task-aggregate timing is excluded from the equality
  check; the inducer inputs and other state remain compared.
- Ordered memorization, Null alignment, prefix deduplication, observed browser
  errors and invalid-target/replay rejection retain the documented fixes from
  the [earlier audit](behavior-audit.md).
- Prompt wording, explicit terminal-without-objective handling, stricter output
  validation, domain isolation, empty initialization and producer-only files
  follow the refactor and the requested asset policy.

## Coverage and limits

The 12 archived workflows cover a single batch, three executor rounds, two plans
in one subgoal, separate single-node subgoals, empty terminal/continuing plans,
failed final evaluation, repeated repair with long-term promotion, and all four
repair tools. At call boundaries, tests compare budgets, verified histories,
candidate IDs, guidance, short-term contents, frozen long-term context and alias
identities as applicable. They compare final plans, actions and reusable banks.

Additional tests bound empty planning at 11 subgoals times 21 rounds, check
shallow-copy/upsert behavior, and exercise storage/exception boundaries. Earlier
fixtures still cover 36 learning gates, 64 assessment flag combinations, six
memory transitions, checkpoint trajectories and repair/action parsers.

A controlled local shopping-browser probe also verifies an actual invalid key
action, rollback navigation, successful-prefix replay, strict binding truncation
and a subsequent Enter submission. It uses no language model and modifies only
the search query. These checks establish the covered control-flow and state
contracts; they do not prove identical model choices, timing, or full WebArena
benchmark equivalence.
