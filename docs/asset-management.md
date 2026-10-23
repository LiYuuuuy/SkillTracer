# Asset management

New domains start empty. A task uses the domain selected from its WebArena task
definition, such as `shopping`, `shopping_admin`, `reddit`, `gitlab` or `map`.
All skill, component and experience reads and writes stay within that domain.
Skill IDs are local to a domain: `shopping/s1` and `shopping_admin/s1` are
independent assets. Separate state roots separate experiment series.

## Paths

`--state-dir` takes a root, not a domain directory. Its default is `.local/state`.
The runtime creates only the domain it needs:

```text
<state-root>/
  shopping/
    manifest.json          Domain identity and bank names
    skills.txt             Current committed skills
    memory.txt             Committed long-term repair memory, JSONL
    subgoals.txt            Committed task/subgoal guidance, JSONL
    components.txt          Committed purpose/type registry
    provenance.jsonl        References to committed production
  shopping_admin/
    ...

<results-root>/
  shopping/
    274.json
    trajectories/274.json
    assets/274/<attempt>/
      manifest.json
      skills.json
      active_memory.json
      short_term_memory.json
      long_term_memory.json
      subgoal_memory.json
      components.json
      progress.json
      events.jsonl
      model_calls.jsonl
    errors/274.json         If a runtime exception occurred
    debug/274.jsonl         With --debug only
```

With no `--results-dir`, the CLI creates a timestamped root under `.local/runs`.
Every attempt gets a unique timestamp/UUID directory. Result and trajectory
files point to the latest attempt through `assets_dir`. Rerunning retains prior
attempts. Task startup logs print the actual domain state and attempt paths.
Errors before the task domain can be resolved go under `unresolved_errors/`.

## Record production, not initialization

The four learned-asset JSON files have a `produced` list. They contain only
entries generated or changed in the current attempt; an unchanged loaded bank
produces an empty list. `skills.json` additionally contains induction decisions.
Refining an existing skill records that skill's new version. Repeated refinements
in one attempt leave its latest produced version in `skills.json`; the ordered
`skill_induced` events retain each generation transition.

Working memory and short-term memory start empty for each task and subgoal
respectively. Their files contain current task-local values and repair state.
Execution/repair events retain their production and transitions. Loaded long-term
memory and retrieved subgoal guidance are context, and are not copied into these
files. `skill_selected` stores only the selected ID.

`model_calls.jsonl` records agent role, response and provider error. Ordinary runs
omit the prompt because it can repeat a large registry or experience bank.
`--debug` explicitly enables full prompts and AXTree snapshots, with the storage
cost that implies. Browser observations and current-task plans/actions remain in
the execution journal for attribution and rollback inspection.

No task records initial/current/candidate copies of cross-task banks or lists
which assets were initialized. Persistent space consists of the current banks,
actual produced versions and task traces. For fixed work and no new learning,
increasing the loaded bank does not increase an ordinary attempt's serialized
asset payload. An offline regression checks this with 0 versus 1,000 entries in
each bank. Banks are still loaded in RAM for the original algorithms; this policy
does not change retrieval or introduce lazy loading.

## Commit and provenance

Generation and reuse are distinct. A failed task may have produced a candidate
skill or repair memory that remains in its attempt directory without entering a
reusable bank. `manifest.json` records a separate flag for each bank write:

| Bank | Commit point |
| --- | --- |
| Components | Eligible subgoal boundary, including tasks that fail later |
| Skills | Successful final task evaluation |
| Long-term memory | Successful final task evaluation |
| Subgoal experience | Successful final task evaluation; non-Null guidance only |

The original learning gates and repair transitions are documented in
[learning behavior](learning-behavior.md). A successful task does not necessarily
produce every kind of asset. Skill retrieval starts with a nonempty domain bank;
the default `top_k=3` limits candidates without imposing a minimum of three.

Each domain's append-only `provenance.jsonl` records committed production only:

```json
{
  "schema_version": 2,
  "domain": "shopping",
  "bank": "skills",
  "task_id": "274",
  "source": "/workspace/results/shopping/assets/274/<attempt>/skills.json",
  "entities": [{"id": "s1", "sha256": "<content fingerprint>"}]
}
```

Timestamps accompany real records. Assets without a natural ID use their content
fingerprint as the ID. Payloads stay in producer files. Unchanged bank writes do
not add entries to this journal; repeated commits do not re-journal earlier type
discoveries in the same attempt. `bank_commits` reports a completed write routine,
so its value can be true even when nothing new was produced.

There is no transaction spanning bank writes. A later write failure retains
earlier committed banks and accurate per-bank flags. Evaluation results are saved
before the success-gated bank routines; trajectories follow those routines, as
in the original workflow. A bank failure can therefore leave an evaluation result
without a new trajectory. Its attempt directory still retains execution and the
error state. Attempt files use atomic
replacement; journals append transitions. They support inspection rather than
automatic task resumption. Run tasks sequentially against a shared state root;
concurrent writers to the same domain are not coordinated.

## Inspect and migrate

Inspection does not initialize domains or require API credentials:

```bash
python -m skilltracer.assets inspect --state-dir .local/state
```

A legacy root with flat `skills.txt`, `memory.txt`, `subgoals.txt` or
`components.txt` is rejected by the runtime rather than loaded into an arbitrary
domain. Migrate it explicitly:

```bash
python -m skilltracer.assets migrate \
  --state-dir .local/old/state \
  --results-dir .local/old/results \
  --backup-dir ../backup/assets
```

Migration copies every legacy bank to a unique backup directory and verifies its
SHA-256 hash. It examines attempt manifests whose state path exactly matches the
old root, whose domain is known, and whose per-bank commit flag permits recovery.
It imports only `produced` entries, or the explicit `learned`/`staged` deltas of
the earlier asset format. Initial/current banks and successful result status
alone are not evidence of production. Committed types from failed tasks remain
eligible; uncommitted skills and experience do not.

Recovery is prepared in a staging directory before active banks change. A
failure preserves the flat banks and restores any existing scoped banks.
New skill IDs are normalized within each domain; the provenance record keeps
`source_skill_id` and the original producer path. The backup's `migration.json`
records source paths, hashes and recovery counts. A small `migration.json` in
the state root points to that report. Repeating a completed migration is a no-op.

The old mixed banks remain archived without guessing domains from skill names
or browser IDs. They are not automatically imported. Historical attempt files
are preserved as evidence in their original format; all new attempts use the
producer-only schema version 2. Backups and runtime data are excluded from the
source archive, wheel and source distribution.
