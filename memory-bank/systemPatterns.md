# System Patterns

## Architectural Decisions
<!-- YYYY-MM-DD — Decision — Why -->
- 2026-05-16 — _PipelineRunner pattern — per-run mutable state lives on a private single-use executor class; Pipeline.run() is a 3-line delegator. Keeps Pipeline clean for subclassing.

## Recurring Patterns
- <PATTERN>

## Debugging Insights
<!-- YYYY-MM-DD — Bug — Resolution -->
- 2026-05-16 — optimize_run returns (output, score) but score was never passed to store_memory; all memory rows had NULL score. Fixed by deferring store_memory to _finalize() and passing score= directly.

---
Read trigger: WHEN modifying architecture or adding a new system integration.
Updated: WHEN a novel pattern emerges; propose append and wait for confirmation.
