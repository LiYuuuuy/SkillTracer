# Programmatic edge checks

Edges remain in the full plan used by planning, assessment, skill storage and
prefix commits. Executor receives only the node projection and completion
metadata. Its prompt has no edge instructions, and its output cannot determine
whether an edge condition is true.

## Matching rules

Both predicates read the **current complete AXTree observation**, rather than
the planner's evidence slice, a tree delta or working memory. Strings are
normalized with Unicode NFKC, case folding and collapsed whitespace.

| Predicate | Match | Example |
| --- | --- | --- |
| `exists_component(component_type)` | Exact equality with an AXTree role token | `exists_component('combobox')` matches a combobox node, not a label containing that word |
| `exists_content(target_content)` | Substring within one observed text field: name, value, label, text or description | `exists_content('Search results')` matches a results heading |

Component matching uses observed roles, not semantic component registry names.
Content matching excludes node IDs, role names and URLs and never joins text
from separate nodes. Evidence identifies the matched node and role or text
field. This is deterministic string matching, without semantic inference.

An edge may use a singular `precondition` string or a `preconditions` list.
Each predicate receives a separate boolean result; an unconditional edge has
no predicate result. Arguments must be one nonempty literal string, positional
or using the predicate's named parameter shown above. Expressions are parsed,
never executed. Invalid predicates return `false` with an `error` description.

## Timing and assessment input

Execution follows the validated pending node order. Before the next unfinished
node, the runtime checks its incoming edges whose source nodes have completed.
It uses the observation after the preceding successful action, including when
the executor batch ends there. At the next batch, it checks that boundary again
against the fresh page. Nonadjacent edges wait until their target is next.
A failed source action does not reach the following boundary.

`ExecutionService.describe()` appends the results to the existing
`delta_axtree_traj` string, after the action and page trajectory:

```text
[PROGRAM_EDGE_CHECKS]
[
  {
    "from": "n2",
    "to": "n3",
    "url": "http://localhost:7770/catalogsearch/result/?q=usb+wifi",
    "condition": "exists_content('Search results')",
    "result": true,
    "evidence": [{"bid": "52", "field": "name", "value": "Search results for: 'usb wifi'"}]
  }
]
```

Both existing Summarizer stages receive this same trajectory through their
existing input. Results are assessment evidence. They do not directly stop
actions, force the repair stage or add a retry policy. The six judge flags,
repair protocol and public agent interfaces remain unchanged. The existing
attempt `events.jsonl` also records each result as `edge_validated`.

Offline regression tests in `tests/test_edge_conditions.py` cover matching,
literal escaping, same-batch and cross-batch observations, target timing,
edge-free binding and delivery to both assessment stages.
