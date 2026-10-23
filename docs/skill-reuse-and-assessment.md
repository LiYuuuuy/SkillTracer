# Enforced skill reuse and two-stage assessment

These policies define current behavior. Historical source audits are reference
material, not a requirement to imitate legacy behavior in future changes unless
the user explicitly requests that comparison.

## Planner decision and graph management

Planner output retains its existing plan/evidence text blocks. The plan object
adds reuse fields only when a matching skill is available:

```json
{"use_skill": true, "skill_start_node_id": "n2", "nodes": [], "edges": [], "next_step_decision": {"should_plan_next": false}}
```

The empty graph above illustrates the field locations, not a valid reuse: a
claimed reuse must contain at least one new skill node. `Planner.plan()` keeps
the same arguments and returns text. The selected skill is available on every
planning round of the subgoal, including replanning. When retrieval returns no
match, the planner receives no skill content, reuse instructions, skill label,
or reuse fields in its example output. An empty bank follows the same path.

With `use_skill=true`, `skill_start_node_id` identifies the original skill node
that the current page state is immediately **before**. This is the next node to
execute, not the last completed node, and its ID is taken from the injected
skill before any runtime renumbering. The planner must use page observations
and verified progress to locate this position. With false, the entry is null.

`planning/reuse.py` runs before executor binding:

1. With `use_skill=false`, validate the ordinary plan and recover colliding IDs
   against the verified prefix. A missing field is treated as false for existing
   callers and stored responses. See the [node recovery rules](node-identity-and-retrieval.md).
2. With true, compare the new graph to the remaining skill from the reported
   entry. The first new node must match that entry; subsequent nodes may be a
   partial induced subgraph of the remaining skill. The baseline is the
   verified task history plus verified current-subgoal progress. Accept both
   a suffix-only graph and a cumulative graph containing an unchanged prefix.
3. Keep a matching graph. Reject mutations to the retained prefix. Otherwise
   discard the proposed graph and append the skill suffix **including the
   reported entry node** to the baseline. Earlier skill nodes are excluded.
4. Renumber appended nodes after the largest retained `nN`, and remap edge
   endpoints. The baseline stays committed; only the new suffix is executable.

For example, a skill `n1 -> n2 -> n3` and entry `n2` means append `n2 -> n3`
on a mismatch. Both node bodies and the internal edge conditions remain
unchanged; the incoming edge from the omitted `n1` is removed. Skill completion
metadata is retained. An entry at `n3` still executes `n3`.

Unknown IDs, runtime-renumbered IDs, null and nonstring entries are rejected as
entry hints and force a full-skill replacement, with a recorded reason. An
entry cannot silently skip a different first planner node. Stored responses
that omit the entry field retain their prior subgraph check; a mismatch falls
back to the full skill. Neither the injected skill nor verified history is
mutated. The program validates structural consistency; it cannot establish
that a model's claimed page position is semantically correct.

A match is an order-preserving induced subgraph. Node IDs may differ; every
other node field must match exactly, including action, purpose, component type,
description and any additional fields. Each internal edge and all its attributes
must match, including preconditions. Missing internal conditions, new edges,
changed text and reversed node order trigger replacement. This deliberately
prevents deleting an internal skill edge while claiming its adjacent nodes were
copied. A plain bridge from the verified boundary is composition metadata;
it is not part of the skill subgraph.

There is no semantic paraphrase matching or action adaptation in this check.
The planner prompt explicitly requires exact reuse. Full reuse, partial reuse
and disconnected induced subgraphs are supported. An invalid selected skill or
true without any injected skill is an error; there is no trustworthy graph to
append in those cases. Nonboolean `use_skill` is rejected.

Verified commits for a managed skill retain its edge conditions across executor
rounds. The final executed graph keeps these conditions and its completion
metadata. The repair agent can still propose a separate execution-time repair;
this does not relax the planner's reuse check.

`skill_reuse_checked` events record the decision, replacement reason, skill ID,
effective original `skill_start_node_id` and runnable-node-to-skill-node mapping.
They do not copy an initialized skill bank. Normal producer-only task asset
paths and bank commit rules remain active.

## Two assessment stages remain separate

The first summarizer request uses `judge.md` and returns six boolean items.
The program builds the named flag dictionary. Plain boolean lines, six numbered
items or six named flags are accepted. Numbers and names determine alignment;
duplicate or incomplete sets are rejected. Boolean words inside an observation
sentence are not counted as flag values.

Successful, nonempty execution returns the existing `Assessment` immediately.
An action failure or empty execution enters the second request, using
`repair.md`. Actual browser errors force that repair branch even if the model
claims success. This policy does not merge the two stages or add per-field model
requests.

The second request emits fields, not JSON:

```text
error_node: n2
repair_tool: REFINE_DESCRIPTION
node_id: n2
new_description: |
  Select the observed "Bob's offer" entry.
evidence: |
  [10] link 'Bob's "offer"' at C:\Users\new {raw: text}
  error_node: this is observed text, not a control field
summary: |
  The previous target was absent.
insight: |
  Use the observed entry.
subgoal_guidance: |
  Null
```

For each `|` block, all content lines must be indented. Quotes, backslashes,
braces, code fences and field-like lines remain literal text. The parser does
not interpolate these values into JSON or parse observed JSON snippets as
control data. The program assembles `error_evidence` and `repair_signal.arguments`
as dictionaries, and standard JSON serialization escapes them when saving.
Inline field values and prior section-based responses remain accepted. Valid
legacy JSON responses remain supported; malformed JSON is reported as an
`InvalidAssessment` rather than leaking a JSON decode failure into execution.

`parse_repair()` still returns `(repair_dict, guidance_string)`. The public
`summarize()` signature, `Assessment` fields and `to_dict()` shape are unchanged.
Its `repair_signal` retains the same tool/arguments structure consumed by repair
execution and memory. Provider, browser and storage errors still propagate;
only actual assessment validation errors enter the replan path.
Invalid repair node references now use the first uncommitted current node as a
recorded fallback. Conflicting valid targets and invalid tools or parameters
remain rejected. Prompt examples use the current pending IDs.

## Validation

Tests cover graph replacement, partial/cumulative reuse, remapped IDs, duplicate
node content, prefix protection, edge conditions across executor rounds, and
ordinary planning. Entry tests cover inclusive middle/final nodes, invalid and
contradictory entry IDs, preserved completion metadata, duplicate node bodies,
continued execution without repeating a committed prefix, and prompt omission
for both empty banks and explicit retrieval misses.
Literal-field tests exercise quotes, invalid JSON fragments,
backslashes, code fences, control characters, Unicode and embedded field names.
Separate tests assert one model request on judge success and two on repair, and
check public signatures and serialized return objects.

Local probes include a real-provider search workflow, a separate real-provider
two-stage assessment with special-character observations, and a controlled real
browser workflow that injects a planner violation plus an invalid browser key.
The latter checks forced replacement, rollback and subsequent successful search.
These validate the policies and interfaces; a forced reuse of an unsuitable
navigation procedure can still fail the underlying browser task.

The entry probe uses the configured real planner after a real browser has filled
the search query. It reports original node `n2`, immediately before submission.
A controlled description deviation then triggers replacement with only `n2`;
the browser submits the retained query without filling it again. Other roles in
this probe are controlled, and evaluation checks the actual results URL. A
separate provider call with no selected skill omits reuse instructions and fields.
Both probes use isolated validation paths and leave default state banks unchanged.
