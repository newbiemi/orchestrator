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
12. [Data Storage](#12-data-storage)
13. [Known Behaviours & Gotchas](#13-known-behaviours--gotchas)

---

## 1. Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in your API key
cp .env.example .env

# Run a pipeline
python runner.py --pipeline trading --ticker AAPL

# Run without eval/optimizer (faster)
python runner.py --pipeline trading --ticker AAPL --no-eval

# Summarize a project directory
python runner.py --pipeline context_summarizer --path ./myproject

# Start the API server
python runner.py --serve --port 8000

# List configured pipelines
python runner.py --list

# View recent run logs
python runner.py --logs
python runner.py --logs --pipeline trading -n 50
```

---

## 2. Architecture Overview

```
runner.py / api.py
      │
      ▼
core/pipeline.py  ←─── core/config.py (pipelines.yaml)
  Pipeline.run()
      │
      ├─ _inject_memory_context()   ← core/memory.py (memory.db)
      │
      ├─ [node 1]  OllamaNode       ← core/ollama_node.py
      │     └─ log_run()            ← core/logger.py   (runs.db)
      │
      ├─ [node 2]  ClaudeNode       ← core/claude_node.py
      │     └─ log_run()
      │
      ├─ [eval]   EvalMarker        ← triggers prompt_optimizer.py
      │     └─ score → mutate → retry if below threshold
      │
      └─ store_memory()             ← core/memory.py
```

**Data flow:** each node's output is passed as `input_text` to the next node.

**Two ways to define a pipeline:**
- **YAML-only** — add a block in `config/pipelines.yaml`, use `ConfiguredPipeline.from_yaml()`
- **Python subclass** — subclass `Pipeline`, implement `build_nodes()` for custom logic (e.g. fan-out chunking)

---

## 3. CLI Commands

| Command | Description |
|---------|-------------|
| `python runner.py --pipeline <name>` | Run a pipeline |
| `--ticker AAPL` | Ticker symbol (trading pipeline) |
| `--path ./dir` | Directory path (context_summarizer) |
| `--input "text"` | Arbitrary initial input (generic pipelines) |
| `--no-eval` | Skip eval/optimizer nodes for speed |
| `--list` | List all pipelines defined in pipelines.yaml |
| `--logs` | Show recent run log (all pipelines) |
| `--logs --pipeline trading` | Filter logs by pipeline |
| `-n 50` | Number of log rows to show (default 20) |
| `--serve` | Start FastAPI server |
| `--port 8000` | Server port (default 8000) |

---

## 4. Pipeline Configuration (YAML)

All pipelines live in `config/pipelines.yaml`. Full schema:

```yaml
pipelines:

  my_pipeline:
    description: "Human-readable label"
    triggers:
      - cli          # runnable via runner.py
      - cron         # can be scheduled
      - api          # exposed via FastAPI
    memory_context_n: 5          # past runs injected into first node (0 = disabled)
    prompt_optimizer:
      enabled: true              # run eval + mutation after last node
      threshold: 7               # min score (1–10) to accept output
      max_retries: 2             # max mutation retries per run
    nodes:
      - name: my_ollama_step
        type: ollama
        model: qwen2.5-coder
        system: "You are a helpful assistant."
        prompt_template: |
          Summarize this:
          {input}
        temperature: 0.1

      - name: my_claude_step
        type: claude
        model: claude-sonnet-4-6
        system: "You produce structured output."
        prompt_template: |
          Based on the summary, produce a report:
          {input}
        max_tokens: 1024
        temperature: 0.2

      - name: eval
        type: eval
        threshold: 7             # overrides pipeline-level threshold for this node
```

**`{input}` placeholder** — replaced at runtime with the previous node's output (or `initial_input` for the first node).

**`prompt_variants`** — appended automatically by the optimizer when a winning prompt is found. Never edit manually.

---

## 5. Node Types

### OllamaNode
Calls a locally-running Ollama model via HTTP.

| Field | Default | Notes |
|-------|---------|-------|
| `model` | `qwen2.5-coder` | Any model pulled in Ollama |
| `system` | `"You are a helpful assistant."` | System prompt |
| `temperature` | `0.2` | 0.0–1.0 |
| `prompt_template` | `None` | If omitted, input is passed as-is |

- Requires Ollama running at `OLLAMA_BASE_URL` (default `http://localhost:11434`)
- **Circuit breaker**: after 3 consecutive failures, fails fast for 60s instead of waiting 120s timeout
- Sync HTTP — safe for CLI, cron, and FastAPI

### ClaudeNode
Calls the Anthropic API.

| Field | Default | Notes |
|-------|---------|-------|
| `model` | `claude-sonnet-4-5` | Any valid Claude model ID |
| `system` | `"You are a helpful assistant."` | System prompt |
| `max_tokens` | `1024` | Response token cap |
| `temperature` | `0.2` | 0.0–1.0 |
| `prompt_template` | `None` | If omitted, input is passed as-is |

- **Fallback**: if `ANTHROPIC_API_KEY` is missing, automatically delegates to `OllamaNode` using `OLLAMA_FALLBACK_MODEL`. The `NodeResult.model` field records `"<model> [claude-fallback]"` so logs show what actually ran.

### EvalMarker
Not a real LLM node — a sentinel that triggers the prompt optimizer on the previous node's output.

```yaml
- name: eval
  type: eval
  threshold: 7
```

- Place it **after** the node you want to evaluate
- Skip with `--no-eval` flag at CLI or `no_eval=True` kwarg in code

---

## 6. Built-in Pipelines

### `trading`
Fetches 30-day EOD price data → Ollama summary → Claude BUY/HOLD/SELL decision.

```bash
python runner.py --pipeline trading --ticker AAPL
python runner.py --pipeline trading --ticker BTC-USD --no-eval
```

Output format: `DECISION: BUY — <one sentence reason>`

Set `ALPACA_EXECUTE=true` in `.env` to log trade intent (execution is stubbed — requires `alpaca-trade-api`).

### `context_summarizer`
Walks a directory, chunks source files, summarizes with Ollama, then produces a structured CLAUDE.md-ready summary with Claude.

```bash
python runner.py --pipeline context_summarizer --path ./myproject
```

Output written to `<path>/context/summary.md`.

Supported file types: `.py .ts .tsx .js .jsx .md .yaml .yml .toml .json .sql .sh .env.example`

Excluded: `node_modules`, `__pycache__`, `.git`, `venv`, `dist`, `build`, lock files.

---

## 7. Memory System

Stored in `context/memory.db` (SQLite).

**Per pipeline run:**
1. **Recall** — loads the last `memory_context_n` runs and prepends them to the first node's system prompt as context
2. **Store** — saves the final output (+ score if eval ran) after a successful run

**Schema (simplified):**
```
memories: id, pipeline, input_hash, input_preview, output, score, timestamp
```

**Tuning:**
- Set `memory_context_n: 0` in YAML to disable memory for a pipeline
- Memories with scores are preferred when `min_score` filtering is used
- `input_hash` (SHA-256) enables deduplication for identical inputs

---

## 8. Prompt Optimizer

Runs after the node preceding an `eval` marker.

**Flow:**
```
score output
  │
  ├─ score >= threshold → log as winner, save prompt variant to pipelines.yaml
  │
  └─ score < threshold → mutate prompt (ask Ollama) → rerun node → re-score
        └─ repeat up to max_retries times
```

**Scoring (combined):**
- **Rule-based** (60% weight): pipeline-specific format check (e.g. trading must contain `DECISION: BUY/HOLD/SELL`)
- **Self-eval** (40% weight): Ollama rates output 1–10 given the input

Always uses Ollama for eval and mutation — never Claude — to minimize API cost.

**Winning prompts** are appended to `pipelines.yaml` under the node's `prompt_variants` list. The file is written with `ruamel.yaml` so comments and formatting are preserved.

**Skip it:** `--no-eval` flag or set `prompt_optimizer.enabled: false` in YAML.

---

## 9. FastAPI Server

```bash
python runner.py --serve --port 8000
# Docs at http://localhost:8000/docs
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/pipelines` | List all configured pipelines |
| `POST` | `/run/{pipeline_name}` | Run a pipeline, returns structured JSON |
| `GET` | `/logs` | Recent runs (all pipelines) |
| `GET` | `/logs/{pipeline_name}` | Recent runs for one pipeline |

### POST /run/{pipeline_name}

Request body:
```json
{
  "input": "AAPL",
  "ticker": "AAPL",
  "path": null
}
```

Response:
```json
{
  "pipeline": "trading",
  "status": "ok",
  "output": "DECISION: HOLD — ...",
  "steps": [
    { "node": "summarizer", "model": "qwen2.5-coder", "token_count": 312, "latency_ms": 4200, "output_preview": "..." },
    { "node": "decision",   "model": "claude-sonnet-4-5", "token_count": 89, "latency_ms": 1100, "output_preview": "..." }
  ],
  "total_tokens": 401,
  "total_latency_ms": 5300,
  "error": null
}
```

---

## 10. Adding a New Pipeline

### Option A — YAML only (simple linear chains)

Add a block to `config/pipelines.yaml`. It's immediately available via CLI and API with no Python changes.

### Option B — Python subclass (custom logic)

```python
# pipelines/my_pipeline.py
from core.pipeline import Pipeline
from core.claude_node import ClaudeNode
from core.ollama_node import OllamaNode

class MyPipeline(Pipeline):
    name = "my_pipeline"

    def build_nodes(self, **kwargs):
        return [
            ("step_one", OllamaNode(model="qwen2.5-coder", system="...")),
            ("step_two", ClaudeNode(model="claude-sonnet-4-6", system="...")),
        ]
```

Then register it in `runner.py` (`_run_pipeline`) and `api.py` (`_run_pipeline`):
```python
if name == "my_pipeline":
    from pipelines.my_pipeline import MyPipeline
    MyPipeline().run(initial_input=args.input, no_eval=no_eval)
```

---

## 11. Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes* | — | Claude API key. If absent, ClaudeNode falls back to Ollama |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_FALLBACK_MODEL` | No | `qwen2.5-coder` | Model used when Claude fallback activates |
| `ALPACA_API_KEY` | No | — | Alpaca trading key (stubbed) |
| `ALPACA_SECRET_KEY` | No | — | Alpaca secret (stubbed) |
| `ALPACA_BASE_URL` | No | `https://paper-api.alpaca.markets` | Alpaca endpoint |
| `ALPACA_EXECUTE` | No | `false` | Set to `true` to log trade intent |

---

## 12. Data Storage

| File | Contents | Reset to clear |
|------|----------|----------------|
| `context/runs.db` | All node execution logs (pipeline, node, model, tokens, latency, status) | Delete file |
| `context/memory.db` | Pipeline run memories used for context injection | Delete file |
| `core/logs/optimizer.db` | Prompt optimizer attempt log (scores, mutations) | Delete file |
| `config/pipelines.yaml` | Pipeline definitions + winning prompt variants | Edit manually |
| `context/summary.md` | Latest context_summarizer output | Re-run pipeline |

---

## 13. Known Behaviours & Gotchas

**Ollama must be running** before any pipeline that uses an OllamaNode. Circuit breaker will fail fast after 3 consecutive failures (60s cooldown) rather than hanging for 2 minutes each time.

**Claude fallback is silent** unless you check the `model` field in logs — it will show `qwen2.5-coder [claude-fallback]` if the API key was missing.

**`--no-eval` saves significant time** — each eval run adds 1–3 Ollama calls (score + optional mutation + retry). Use it during development or when latency matters.

**`context_summarizer` runs Ollama N times** (once per file chunk) before the base pipeline starts. Large projects with many files will be slow.

**`save_winning_prompt` modifies `pipelines.yaml` in-place** using ruamel.yaml, which preserves all comments and formatting. The config cache is automatically invalidated after each write.

**Memory injection grows the system prompt** of the first node by up to `memory_context_n * ~700 chars`. Reduce `memory_context_n` if you're hitting token limits on small Ollama models.

**SQLite is not safe for concurrent writes** — don't run multiple pipeline instances against the same `runs.db` simultaneously.
