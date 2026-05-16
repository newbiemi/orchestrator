# Project Context Summary
Generated: 2026-05-16 18:20
Root: `C:\Users\fahms\projects-portfolios\personal_projects\orchestrator`

> **WARNING: This summary may be stale.** 5 file(s) modified since last generation: C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\pipeline.py, C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\activeContext.md, C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\planLog.md, C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\progress.md, C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\systemPatterns.md
> Re-run `python ~/context_prep.py` to refresh.

## Python Files

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\api.py**
- Lines: 151
- Preview (first 10 lines):
```python
"""
FastAPI server for orchestrator.
Activate with: python runner.py --serve [--port 8000]

Endpoints:
  POST /run/{pipeline_name}   — run a pipeline, returns structured JSON
  GET  /pipelines             — list all configured pipelines
  GET  /logs/{pipeline_name}  — recent runs for a pipeline
  GET  /health                — liveness check
"""
```
- Classes:
  - `RunRequest`
  - `StepLog`
  - `RunResponse`
- Functions:
  - `_pipeline_result_to_response(name: str, result) -> RunResponse`
  - `_run_pipeline(name: str, req: RunRequest)`
  - `health()`
  - `get_pipelines()`
  - `run_pipeline(pipeline_name: str, req: RunRequest)`
  - `get_logs(pipeline_name: str, limit: int=20)`
  - `get_all_logs(limit: int=20)`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\__init__.py**
- Lines: 7
- Preview (first 10 lines):
```python
from .pipeline import Pipeline, PipelineResult, ConfiguredPipeline, EvalMarker
from .claude_node import ClaudeNode, NodeResult
from .ollama_node import OllamaNode
from .logger import log_run, fetch_runs, init_db
from .config import load_pipeline_config, list_pipelines, get_node_configs
from .memory import store_memory, recall_memories, format_memories_for_context
from .prompt_optimizer import optimize_run, compute_score
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\claude_node.py**
- Lines: 141
- Preview (first 10 lines):
```python
"""
Claude node — wraps the Anthropic SDK.
Each call returns a NodeResult with output text + metadata.

If ANTHROPIC_API_KEY is missing/empty at call time, automatically falls back
to Ollama using OLLAMA_FALLBACK_MODEL (default: qwen2.5-coder).
The fallback is transparent — NodeResult format is identical; model field
records "<ollama-model> [claude-fallback]" so logs show what actually ran.
"""
import os
```
- Constants:
  - `DEFAULT_MODEL = 'claude-sonnet-4-5'`
  - `DEFAULT_MAX_TOKENS = 1024`
  - `FALLBACK_MODEL_ENV = 'OLLAMA_FALLBACK_MODEL'`
  - `FALLBACK_MODEL_DEFAULT = 'qwen2.5-coder'`
- Classes:
  - `NodeResult`
  - `ClaudeNode`
    - `__init__(self)`
    - `_build_prompt(self, input_text: str) -> str`
    - `_run_fallback(self) -> NodeResult`
    - `run(self) -> NodeResult`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\config.py**
- Lines: 93
- Preview (first 10 lines):
```python
"""
Config loader — reads pipelines.yaml and returns typed dicts.
Single source of truth for all pipeline configuration.
"""
from pathlib import Path
from typing import Any, Optional

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config" / "pipelines.yaml"
```
- Constants:
  - `CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'pipelines.yaml'`
- Functions:
  - `_load_all() -> dict`
  - `load_pipeline_config(name: str) -> dict`
  - `list_pipelines() -> list[str]`
  - `get_node_configs(pipeline_name: str) -> list[dict]`
  - `get_optimizer_config(pipeline_name: str) -> dict`
  - `get_memory_context_n(pipeline_name: str) -> int`
  - `save_winning_prompt(pipeline_name: str, node_name: str, prompt: str, timestamp: str) -> None`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\logger.py**
- Lines: 89
- Preview (first 10 lines):
```python
"""
SQLite logger for all pipeline node executions.
Schema: pipeline_name, node_name, model, input_text, output_text,
        latency_ms, token_count, timestamp
"""
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
```
- Constants:
  - `DB_PATH = Path(__file__).parent.parent / 'context' / 'runs.db'`
- Functions:
  - `_get_conn() -> sqlite3.Connection`
  - `init_db() -> None`
  - `log_run() -> int`
  - `fetch_runs(pipeline: Optional[str]=None, limit: int=20) -> list[dict]`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\memory.py**
- Lines: 152
- Preview (first 10 lines):
```python
"""
Memory layer — stores pipeline run outcomes for continuous learning.

Separate SQLite database (context/memory.db) so memory can be queried,
pruned, or migrated independently from the run log.

Each pipeline run can:
  1. recall() — load the last N relevant memories before calling nodes
  2. store()  — save the run outcome + score after completion
```
- Constants:
  - `DB_PATH = Path(__file__).parent.parent / 'context' / 'memory.db'`
- Functions:
  - `_get_conn() -> sqlite3.Connection`
  - `init_memory_db() -> None`
  - `input_to_hash(text: str) -> str`
  - `store_memory() -> int`
  - `recall_memories(pipeline: str, n: int=5, min_score: Optional[float]=None) -> list[dict]`
  - `format_memories_for_context(memories: list[dict]) -> str`
  - `update_score(memory_id: int, score: float) -> None`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\ollama_node.py**
- Lines: 136
- Preview (first 10 lines):
```python
"""
Ollama node — sync httpx calls to localhost:11434.
Safe for CLI, cron, and FastAPI (both def and async def routes).
"""
import os
import time
from typing import Optional

import httpx
from dotenv import load_dotenv
```
- Constants:
  - `OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')`
  - `DEFAULT_MODEL = 'qwen2.5-coder'`
  - `REQUEST_TIMEOUT = 300.0`
  - `CB_THRESHOLD = 3`
  - `CB_RESET_SECS = 60.0`
- Classes:
  - `OllamaNode`
    - `__init__(self)`
    - `_build_prompt(self, input_text: str) -> str`
    - `run(self) -> NodeResult`
- Functions:
  - `_cb_check() -> None`
  - `_cb_success() -> None`
  - `_cb_failure() -> None`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\pipeline.py**
- Lines: 299
- Preview (first 10 lines):
```python
"""
Base Pipeline — chains nodes, passes output→input, logs the full run.

Two usage patterns:
  1. Subclass Pipeline and implement build_nodes() — full Python control.
  2. Use ConfiguredPipeline.from_yaml(name) — zero Python, driven entirely
     by pipelines.yaml. Good for simple linear chains.

Memory integration (Phase 7):
  Before running nodes, the pipeline recalls the last N relevant memories
```
- Classes:
  - `PipelineResult`
    - `print_summary(self) -> None`
  - `EvalMarker`
    - `__init__(self, threshold: int=7)`
  - `_LastStep`
  - `_PipelineRunner`
    - `__init__(self, pipeline: 'Pipeline', initial_input: str)`
    - `execute(self) -> PipelineResult`
    - `_execute_node(self, node_name: str, node: ClaudeNode | OllamaNode) -> bool`
    - `_dispatch_eval(self, node_name: str, marker: EvalMarker) -> None`
    - `_finalize(self) -> PipelineResult`
  - `Pipeline`
    - `build_nodes(self, **kwargs) -> list[tuple[str, ClaudeNode | OllamaNode]]`
    - `_inject_memory_context(self, nodes: list) -> None`
    - `run(self, initial_input: str, **kwargs) -> PipelineResult`
  - `ConfiguredPipeline`
    - `__init__(self, name: str)`
    - `from_yaml(cls, name: str) -> 'ConfiguredPipeline'`
    - `build_nodes(self, **kwargs) -> list[tuple[str, 'ClaudeNode | OllamaNode | EvalMarker']]`
- Functions:
  - `_node_from_config(cfg: dict) -> tuple[str, 'ClaudeNode | OllamaNode | EvalMarker']`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\core\prompt_optimizer.py**
- Lines: 345
- Preview (first 10 lines):
```python
"""
Prompt Optimizer — Phase 8

After each pipeline run:
  1. Score the output (rule-based format check, then Ollama self-eval)
  2. If score >= threshold → save prompt variant as "winner" in pipelines.yaml
  3. If score < threshold → mutate prompt (ask Ollama to improve it) and retry
  4. Max 2 retries per run to control cost and latency

Scoring priority:
```
- Constants:
  - `OPT_DB_PATH = Path(__file__).parent / 'logs' / 'optimizer.db'`
- Functions:
  - `_get_opt_conn() -> sqlite3.Connection`
  - `_init_opt_db() -> None`
  - `_log_optimizer() -> None`
  - `register_rule(pipeline: str)`
  - `_score_trading(output: str) -> tuple[float, str]`
  - `rule_based_score(pipeline: str, output: str) -> Optional[tuple[float, str]]`
  - `self_eval_score(input_text: str, output_text: str) -> tuple[float, str]`
  - `compute_score(pipeline: str, input_text: str, output_text: str) -> tuple[float, str]`
  - `mutate_prompt(current_prompt: str, input_text: str, output_text: str, score: float, reason: str) -> str`
  - `optimize_run() -> tuple[str, float]`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\pipelines\__init__.py**
- Lines: 0
- Preview (first 10 lines):
```python

```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\pipelines\context_summarizer.py**
- Lines: 189
- Preview (first 10 lines):
```python
"""
Context Summarizer Pipeline — Phase 4

Flow:
  1. Walk a directory, collect source files (code + markdown)
  2. Chunk files into batches that fit Ollama's context
  3. OllamaNode — summarize each chunk
  4. ClaudeNode — produce a structured CLAUDE.md-ready summary
  5. Write output to <path>/context/summary.md
```
- Constants:
  - `INCLUDE_EXTENSIONS = {'.py', '.ts', '.tsx', '.js', '.jsx', '.md', '.yaml', '.yml', '.toml', '.json', '.env.example', '.sql', '.sh'}`
  - `EXCLUDE_DIRS = {'__pycache__', 'node_modules', '.git', '.next', 'dist', 'build', 'venv', '.venv', 'env', 'backtest_reports'}`
  - `EXCLUDE_FILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'summary.md', 'session_log.md'}`
  - `MAX_FILE_CHARS = 8000`
  - `CHUNK_CHARS = 24000`
- Classes:
  - `ContextSummarizerPipeline`
    - `build_nodes(self, **kwargs) -> list[tuple[str, ClaudeNode | OllamaNode]]`
    - `run(self, initial_input: str='.', **kwargs)`
- Functions:
  - `collect_files(root: Path) -> list[Path]`
  - `files_to_chunks(files: list[Path], root: Path) -> list[str]`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\pipelines\px_dashboard.py**
- Lines: 88
- Preview (first 10 lines):
```python
"""
PX Dashboard Sync Pipeline

Flow:
  1. Run sync_2026_data() from px_dashboard/ingest.py (subprocess)
  2. ClaudeNode — parse sync output and produce a structured sync report

CLI:
  python runner.py --pipeline px_dashboard
  python runner.py --pipeline px_dashboard --no-eval   # skip eval node
```
- Constants:
  - `INGEST_SCRIPT = Path(__file__).resolve().parents[2] / 'influx_coding' / 'dashboard' / 'px_dashboard' / 'ingest.py'`
  - `INGEST_DIR = INGEST_SCRIPT.parent`
- Classes:
  - `PxDashboardPipeline`
    - `build_nodes(self, **kwargs)`
    - `run(self, initial_input: str='', **kwargs)`
- Functions:
  - `run_sync() -> str`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\pipelines\trading.py**
- Lines: 168
- Preview (first 10 lines):
```python
"""
Trading pipeline — Phase 3

Flow:
  1. Fetch EOD price data via yfinance
  2. OllamaNode (qwen2.5-coder) — summarize price action
  3. ClaudeNode (claude-sonnet-4-5) — BUY / HOLD / SELL decision
  4. Log everything to SQLite (via core/logger.py)
  5. Optional Alpaca execution flag (ALPACA_EXECUTE=true in .env)
```
- Constants:
  - `VALID_DECISIONS = {'BUY', 'HOLD', 'SELL'}`
- Classes:
  - `TradingPipeline`
    - `build_nodes(self, **kwargs) -> list[tuple[str, ClaudeNode | OllamaNode]]`
    - `run(self, initial_input: str='AAPL', **kwargs) -> None`
- Functions:
  - `parse_decision(text: str) -> dict`
  - `fetch_eod_data(ticker: str, days: int=30) -> str`
  - `maybe_execute_alpaca(ticker: str, decision: str) -> None`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\runner.py**
- Lines: 186
- Preview (first 10 lines):
```python
"""
Unified entry point for orchestrator.

Usage:
  python runner.py --list                              # list all configured pipelines
  python runner.py --pipeline trading --ticker AAPL   # run trading pipeline
  python runner.py --pipeline context_summarizer --path ./myproject
  python runner.py --logs [--pipeline trading] [-n 20]
"""
import argparse
```
- Functions:
  - `main()`
  - `_run_pipeline(args)`
  - `_start_server(port: int=8000)`
  - `_list_pipelines()`
  - `_show_logs(pipeline, limit)`

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\scripts\auto_summarize_all.py**
- Lines: 212
- Preview (first 10 lines):
```python
#!/usr/bin/env python3
"""
auto_summarize_all.py — Autonomously summarize all project folders.

Flow:
  1. Scan ~/projects for project subdirectories
  2. Skip folders with fresh summaries (< STALE_DAYS old)
  3. Run orchestrator context_summarizer on each stale/missing project
     - Ollama chunks the source files (free, local)
     - Claude produces the structured summary (1 API call per project)
```
- Constants:
  - `PROJECTS_ROOT = Path.home() / 'projects'`
  - `ORCHESTRATOR_ROOT = PROJECTS_ROOT / 'orchestrator'`
  - `STALE_DAYS = 3`
  - `LOG_PATH = PROJECTS_ROOT / 'context' / 'auto_summarize.log'`
  - `SKIP_FOLDERS = {'context', 'orchestrator', '.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env'}`
  - `PROJECT_MARKERS = {'package.json', 'pyproject.toml', 'setup.py', 'requirements.txt', 'CLAUDE.md', 'README.md', '.git'}`
  - `OLLAMA_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')`
  - `OLLAMA_EXE = 'C:\\Users\\fahms\\AppData\\Local\\Programs\\Ollama\\ollama.exe'`
- Functions:
  - `setup_logging() -> None`
  - `is_project(path: Path) -> bool`
  - `is_stale(path: Path) -> bool`
  - `find_projects() -> list[Path]`
  - `ollama_is_up() -> bool`
  - `ensure_ollama() -> bool`
  - `run_pipeline(project_path: Path) -> bool`
  - `main() -> None`

## Markdown Files

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\GUIDE.md**
- Lines: 392
```markdown
# Orchestrator — Reference Guide

> LLM pipeline framework chaining Ollama (local) and Claude (API) nodes.
> Updated: 2026-04-05

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Architecture Overview](#2-architecture-overview)
3. [CLI Commands](#3-cli-commands)
4. [Pipeline Configuration (YAML)](#4-pipeline-configuration-yaml)
5. [Node Types](#5-node-types)
6. [Built-in Pipelines](#6-built-in-pipelines)
7. [Memory System](#7-memory-system)
8. [Prompt Optimizer](#8-prompt-optimizer)
9. [FastAPI Server](#9-fastapi-server)
10. [Adding a New Pipeline](#10-adding-a-new-pipeline)
11. [Environment Variables](#11-environment-variables)
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\HOW_TO_USE.md**
- Lines: 136
```markdown
# How to Use Orchestrator

---

## What is this, really?

An LLM takes text in, returns text out. Every call starts from zero — no memory, no awareness of what happened before. And the quality of the output is almost entirely determined by the quality of the input.

The problem with calling Claude directly on raw data is that you're paying for Claude to read everything, most of which is noise. If you paste 30 days of stock prices or 40 source files, Claude spends most of its context window just absorbing data — before it even starts reasoning.

**The insight:** compressing raw data doesn't require good reasoning. It just requires pattern matching. A cheap local model can do that. Claude should only see the signal, not the noise.

```
raw data → [Ollama: compress, free] → summary → [Claude: reason, paid] → decision
```

That's the entire idea. The orchestrator is infrastructure that makes this pattern repeatable, logged, and automatic.

---
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\activeContext.md**
- Lines: 19
```markdown
# Active Context
<!-- updated: 2026-05-16 17:20 -->

## Current Focus
Refine orchestrator function — refactor core/pipeline.py Pipeline.run() and _inject_memory_context() to simplify control flow and clarify the recall → run → score → store loop

## Recent Trajectory
- No prior sessions logged yet.


## Immediate Next Steps
Read core/pipeline.py in full, audit the Pipeline.run() and _inject_memory_context() methods, identify branching/duplication, then refactor for clarity

## Blockers
context/summary.md is stale (6 files modified); memory-bank stubs are empty

---
Read trigger: WHEN session starts.
Updated: overwrite each working session to reflect current state.
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\planLog.md**
- Lines: 15
```markdown
# Plan Log

## 2026-05-16 17:20 — Refine orchestrator function

**Chosen step:** Read core/pipeline.py in full, audit Pipeline.run() and _inject_memory_context(), identify branching/duplication, then refactor for clarity
**Blocker:** context/summary.md is stale (6 files modified); memory-bank stubs are empty

**Considered:**
- Refactor core/pipeline.py Pipeline.run() — chosen
- Populate empty memory-bank stubs (projectbrief, systemPatterns, progress, activeContext) with real content
- Refresh context/summary.md first, then pick a targeted refinement from fresh snapshot

**Trajectory shift:** First real refinement session — shifting from scaffolding setup to active code improvement in the core pipeline execution loop.

---
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\progress.md**
- Lines: 12
```markdown
# Progress Log

## Milestones
<!-- YYYY-MM-DD — Milestone — Notes -->
- 2026-05-16 — Refactored core/pipeline.py: introduced _PipelineRunner + _LastStep, fixed score-persistence bug, strict YAML validation

## Pending Milestones
- <MILESTONE>

---
Read trigger: WHEN asked for project status.
Updated: append a dated line when a milestone is reached. Never modify past entries.
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\projectbrief.md**
- Lines: 17
```markdown
# Project Brief

## Goals
<!-- What is this project trying to achieve? -->

## Target Audience
<!-- Who uses this? -->

## Hard Constraints
- <CONSTRAINT>

## Out of Scope
- <OUT_OF_SCOPE>

---
Read trigger: WHEN session starts.
Updated: only when project scope or hard constraints change.
```

**C:\Users\fahms\projects-portfolios\personal_projects\orchestrator\memory-bank\systemPatterns.md**
- Lines: 16
```markdown
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
```
