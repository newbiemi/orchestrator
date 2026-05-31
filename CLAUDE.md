# orchestrator — LLM Pipeline Framework

## Quick Reference

| Action | Command |
|--------|---------|
| Install | `pip install -e .` |
| Run pipeline | `python runner.py --pipeline trading --ticker AAPL` |
| Run pipeline (no eval) | `python runner.py --pipeline trading --ticker AAPL --no-eval` |
| Summarize project | `python runner.py --pipeline context_summarizer --path .` |
| List pipelines | `python runner.py --list` |
| Start API | `uvicorn api:app --reload` |
| View logs | `sqlite3 context/runs.db "SELECT * FROM runs ORDER BY id DESC LIMIT 10;"` |
| Verify caching | `sqlite3 context/runs.db "SELECT node, cache_creation_input_tokens, cache_read_input_tokens FROM runs ORDER BY id DESC LIMIT 4;"` |

## Token Efficiency — Read This First

- **Check `context/summary.md`** before scanning any source files.
- **`config/pipelines.yaml`** is the single source of truth for all pipeline behaviour — read it before any node or pipeline file.
- Core files are small (50–165 lines each). Read only the file relevant to the task.
- Do NOT read `context/runs.db` directly — use sqlite3 queries.

## Architecture

### Core modules (`core/`)

| File | Purpose |
|------|---------|
| `pipeline.py` | Base `Pipeline` class, `_PipelineRunner` (per-run state), `ConfiguredPipeline` (YAML-driven) |
| `claude_node.py` | Wraps Anthropic SDK. Lazy client, two-block system prompt for caching, Ollama fallback |
| `ollama_node.py` | Sync httpx calls to Ollama. 90s timeout, 1 retry, circuit breaker |
| `python_node.py` | **Algorithm node** — calls a registered Python callable (`fn: module.path`) as a pipeline step. Returns structured JSON or str as NodeResult. Zero LLM, zero variance. Use for deterministic transforms. |
| `config.py` | Loads `pipelines.yaml`. `get_default_claude_model()` is the single model source of truth |
| `logger.py` | SQLite logging to `context/runs.db`. Includes cache metrics columns |
| `memory.py` | Stores and recalls past run summaries for context injection |
| `prompt_optimizer.py` | Ollama-based eval + prompt mutation loop (Phase 8) |

### Pipelines (`pipelines/`)

| Pipeline | Flow |
|----------|------|
| `trading` | yfinance EOD data → **PythonNode** (`summarize_prices`) → Claude BUY/HOLD/SELL |
| `context_summarizer` | Walk directory → chunk files → Ollama per chunk → Claude structured summary |
| `px_dashboard` | PX Google Sheets → MotherDuck → Claude report |
| `trading_scan_daily` | subprocess bot scan → **PythonNode** (`interpret_scan`) → Telegram |

### Data flow
```
runner.py
  └─ Pipeline.run()
       ├─ _inject_memory_context()   # writes node.memory_context (ClaudeNode)
       │                             # or appends to node.system (OllamaNode)
       └─ _PipelineRunner.execute()
            ├─ OllamaNode.run()      # cheap compression step
            ├─ ClaudeNode.run()      # reasoning step, prompt-cached
            └─ EvalMarker            # triggers prompt_optimizer if score < threshold
```

## Key Patterns

- **Model SoT**: `defaults.claude_model` in `pipelines.yaml`. Bump model once there — all pipelines inherit. Never hardcode model strings in Python.
- **Algorithm-first**: For deterministic transforms (structured data → template), use `type: python` + a registered callable. Only use `type: ollama` for genuinely fuzzy tasks (code compression, creative content). This follows projectbrief.md's "Algorithm-first for automation" principle.
- **PythonNode contract**: `fn` is a dotted path (`module.fn`). Callable signature: `fn(input_text: str) -> dict | str`. Dict → JSON-serialized; str → passed through. `model="python"` in logs.
- **Prompt caching**: `ClaudeNode` sends system as a two-block list — Block 1 (base, `cache_control: ephemeral`) + Block 2 (memory, not cached). Run twice within 5 min to get a cache hit.
- **Ollama fallback**: If `ANTHROPIC_API_KEY` is absent, `ClaudeNode` transparently delegates to `OllamaNode`. Model field in logs shows `<model> [claude-fallback]`.
- **Circuit breaker**: After 3 consecutive Ollama failures, fail fast for 60s instead of waiting 90s each time.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (or Ollama fallback) | Anthropic API key |
| `OLLAMA_BASE_URL` | No | Default: `http://localhost:11434` |
| `OLLAMA_FALLBACK_MODEL` | No | Default: `qwen2.5-coder` |
| `ALPACA_EXECUTE` | No | Set `true` to enable Alpaca order stub |

## Ollama Notes

- Default model: `qwen2.5-coder` — keep it; it outperforms `llama3.2:3b` for technical compression.
- If pipelines hang: run `ollama ps` — a large model (e.g. 79B) loaded alongside `qwen2.5-coder` causes VRAM swap and multi-minute stalls. Unload it first.
- `AIAnalyst` in AI-trading is a **planned future class** — not yet implemented. Do not look for it in bot_binance.py.

## Downstream Consumers

| Project | Integration |
|---------|-------------|
| `content-engine` | `sys.path.insert` to orchestrator root + `_orch_cfg.CONFIG_PATH` override to use its own YAML |
| `AI-trading` | Planned: `AIAnalyst.analyze()` → `ClaudeNode` (not yet implemented) |
