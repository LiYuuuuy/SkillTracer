# Input, working memory and final responses

These fixes keep the agent signatures and browser action names unchanged.

## Replacement input

The bundled BrowserGym fork is `0.10.2+skilltracer.2`. Its `fill(bid, value)`
clears the target, enters the requested text and reads the value back. A mismatch
becomes an ordinary browser action error instead of being reported as success.
The context's existing timeout applies. Empty strings, multiline editable
fields and browser email/URL whitespace sanitization are supported.

On the local shopping search widget, a prefilled value could survive a single
native fill and receive appended text. A separate clear followed by fill
reproduces the intended replacement. This correction is inside one primitive;
the planner still emits `fill(bid, value)` and no extra plan nodes are inserted.

## Independent evidence records

`memorize(bid)` still captures the current observed subtree and retains the
plan node's description as `MEMORY_INTENT`. The runtime also supplies its actual
source URL. Every successful capture creates a new task-local memory record,
even if the BID or URL is reused. This retains evidence from pagination,
different order pages and dynamic updates at the same URL.

Record IDs remain stable until explicitly removed. For compatibility, the
first record for BID `1536` is named `1536`; later records use `1536@2`,
`1536@3`, and so on. Undo does not recycle these IDs. These are memory record
identifiers; only IDs in the current AXTree can target browser elements.

`active_memory.json` retains its flat string-to-string mapping. Keys now denote
records, while each value contains its source URL, intent and observed slice.
Prompt rendering labels the record ID and its original observed BID separately:

```text
[MEMORY 1536@2 | OBSERVED BID 1536]
[SOURCE_URL]
http://localhost:7770/catalogsearch/result/?p=2
[MEMORY_INTENT]
Inspect the remaining products.
[AXTREE_SLICE]
[1536] list ...
```

`get_memory_by_bid()` returns the latest capture for compatibility; prompt
rendering and task artifacts retain every surviving record. Memory growth is
proportional to actual captures within a task. Loaded skill or memory banks
are not copied into these records or enumerated at task initialization.

Each runtime write records a `MemoryWrite(node_id, bid, memory_id)` undo token.
Rollback removes only failed-suffix record IDs in reverse write order. Older
pages and verified-prefix records remain available. The journal includes
`memory_id` and, when applicable, `restored_memory_id`. Older execution batches
that carry previous text instead of a record token retain their recovery path.

## Historical working-memory behavior

The original `memory/memory.py` used `_store[bid]`: it had a browser element ID,
but no independent memory record ID or page namespace. An identical raw subtree
could be deduplicated, and prompt output was sorted by BID. The workflow then
wrapped the subtree with `MEMORY_INTENT` from the plan description. This wrapper
also meant the raw-subtree equality check often did not deduplicate subsequent
workflow writes. A later page with the same BID overwrote the earlier evidence.

There was no called working-memory summarizer or semantic merge stage. The
file's depth/node-limited subtree helper was not called by the main workflow.
Rollback deleted memory by BID and could remove older evidence too. The new
record IDs correct these behaviors while retaining subtree extraction and
intent labels.

## Final response collection

The existing `extract_response(actions)` collects both
`send_msg_to_user(text)` and `report_infeasible(reason)` in action order. Both
positional arguments and their corresponding named parameters are supported.
Text remains literal, including Unicode, newlines, quotes and backslashes.
Nonliteral expressions and malformed calls are ignored without evaluation.
The workflow evaluator receives the collected string through its existing
`response` input; no new output fields or completion rules are introduced.
