---
claude_sync: true
project: orchestrator
type: architecture
date: 2026-05-16
---

# Architecture: _PipelineRunner — per-run state separation in core/pipeline.py

## Overview
`Pipeline.run()` previously held all per-run mutable state as loose locals in a 72-line method: `current_input`, `prev_node_instance`, `prev_node_name`, `final_score`, `t_start`, `result`. These were scattered across 7 concerns in one method. The refactor introduced `_PipelineRunner` as a private, single-use executor class that owns all of this state, and `_LastStep` as a dataclass replacing the brittle two-variable sliding window.

## Components
| Class | Role |
|-------|------|
| `Pipeline` | Base class; defines `build_nodes()`, `_inject_memory_context()`, `run()` (3-line delegator) |
| `_PipelineRunner` | Owns per-run state; exposes `execute()`, `_execute_node()`, `_dispatch_eval()`, `_finalize()` |
| `_LastStep` | Dataclass (`name: str`, `node: ClaudeNode \| OllamaNode`) replacing `prev_node_instance` + `prev_node_name` pair |
| `EvalMarker` | Sentinel node; triggers optimizer on the previous node's output |

## Data Flow
```
Pipeline.run(initial_input, **kwargs)
  └─ _PipelineRunner.execute()
       ├─ _inject_memory_context(nodes)       ← prepends recall to first node
       ├─ for each node:
       │    ├─ EvalMarker → _dispatch_eval()  ← optimizer; sets self.final_score
       │    └─ LLM node  → _execute_node()   ← sets self.last_step (_LastStep)
       └─ _finalize()                         ← single store_memory call with score
```

## Known Constraints
- `_PipelineRunner` is module-private and single-use — never reuse an instance across runs.
- `Pipeline.run()` public signature (`initial_input`, `**kwargs` accepting `ticker`/`path`/`no_eval`) is preserved; all callers in `api.py`, `runner.py`, subclasses, and scripts are unchanged.
- `PxDashboardPipeline` overrides `run()` entirely and does not call `super().run()` — it is an outlier and intentionally not touched by this refactor.
