---
claude_sync: true
project: orchestrator
type: decision
date: 2026-05-16
---

# Decision: Score persistence in Pipeline — defer store_memory until after loop

## Context
`Pipeline.run()` calls the prompt optimizer (`optimize_run`) inside the node loop when it hits an `EvalMarker`. The optimizer returns `(optimized_output, score)`. We needed to persist that score to `context/memory.db` so future recall can weight memories by quality.

Two options were available in the existing `memory.py` API:
1. Call `store_memory(...)` early (on "ok" status, inside the loop), then call `update_score(memory_id, score)` after the optimizer returns.
2. Defer `store_memory` until after the full loop ends, passing `score=` directly in a single write.

## Options Considered
1. **Insert then update** — call `store_memory` first, get back `memory_id`, then call `update_score(memory_id, score)` after the eval branch. Pros: uses the existing `update_score` function. Cons: two DB writes per scored run; if the process dies between insert and update, the row has `score=NULL` permanently (orphan row).
2. **Defer and pass directly** — hold off on `store_memory` until `_finalize()`, pass `score=self.final_score` in one call. Pros: single DB write, no orphan NULL rows, simpler control flow. Cons: `update_score` in `memory.py` goes unused (but harmless).

## Decision
We chose **Option 2 (defer + pass directly)** because it matches the run's natural lifecycle — only "ok" runs are stored anyway — and eliminates the orphan-row failure mode entirely.

## Consequences
- Memory rows for scored runs now carry a non-NULL `score` immediately on insert.
- `memory.update_score()` is no longer called by `pipeline.py` but remains in `memory.py` for potential future callers.
- `_finalize()` is the single point of memory persistence; score flows in via `self.final_score` on `_PipelineRunner`.

## Revisit If
- A pipeline ever needs to update a memory score *after* the run completes (e.g., human feedback loop) — at that point `update_score` becomes the right tool.
