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

## The three problems it solves

### 1. Cost — you're paying Claude to sort mail

When you call Claude with raw data, you're using a surgeon to sort mail. Ollama handles the cheap work (summarizing, scoring, compressing). Claude handles the final judgment.

**Concrete example — trading pipeline:**
- Without orchestrator: paste 30 days of OHLCV data into Claude → ~2,000 input tokens
- With orchestrator: Ollama compresses it to a 200-word summary → Claude sees ~300 tokens
- Same decision quality. ~85% fewer input tokens billed.

**Concrete example — context summarizer:**
- 30 source files → 30 Ollama calls (free) → 1 combined summary → 1 Claude call
- Without it: you'd either send everything to Claude or manually pick what to include

### 2. Memory — LLMs start from zero every time

LLMs have no state between calls. The memory system fakes continuity: it stores past outputs in SQLite, then prepends the last N results to the system prompt before each new run.

From the model's perspective, it's reading its own history. It didn't "remember" — you handed it a summary of what it said before.

**Concrete example:** the trading pipeline remembers the last 5 decisions. If it said HOLD on AAPL three times in a row, the next run has that context. The decision becomes more informed without you doing anything.

### 3. Prompt quality — the highest-leverage thing you can change

Output quality is determined by the prompt. Finding a better prompt manually is slow. The optimizer automates it:

1. Score the output (did it follow the format? is it useful?)
2. If below threshold → ask Ollama to rewrite the prompt
3. Retry with the new prompt
4. If better → save it to `pipelines.yaml` automatically

Ollama handles all of this for free. You're not paying Claude to improve Claude's own prompts.

---

## Is it fully autonomous?

**Partially — honest answer:**

| Automated | Still manual |
|-----------|-------------|
| Chains models end-to-end once triggered | Deciding when and what to run |
| Logs every run (tokens, latency, output) | Scheduling (cron, task scheduler) |
| Injects past run memory automatically | Adding new pipelines to YAML |
| Scores outputs, retries if quality is low | Monitoring if something breaks |
| Falls back to Ollama if API key is missing | Reviewing saved prompt variants |
| Saves improved prompts automatically | |

It's a reliable executor, not an agent. It doesn't decide what to analyze or what to do with the result. You do. What it removes is the manual work between "I have raw data" and "I have a logged, memory-informed final answer."

---

## What it can do right now

### Trading analysis
```bash
python runner.py --pipeline trading --ticker AAPL
python runner.py --pipeline trading --ticker BTC-USD --no-eval   # skip optimizer, faster
```
Fetches 30 days of real price data via yfinance → Ollama summarizes → Claude outputs `DECISION: BUY/HOLD/SELL — reason`. Everything logged to SQLite.

### Project context summarizer
```bash
python runner.py --pipeline context_summarizer --path ./myproject
```
Walks the directory, chunks source files, summarizes each chunk with Ollama, then Claude produces a structured `context/summary.md`. This is the file Claude Code reads at session start — it's how you avoid re-reading all files every session.

### Any custom pipeline via YAML
Add a block to `config/pipelines.yaml` — no Python needed. Define nodes, prompts, models, thresholds. It's live immediately.

### API server
```bash
python runner.py --serve --port 8000
# POST http://localhost:8000/run/trading  {"input": "AAPL"}
```
Exposes all pipelines as REST endpoints. Trigger from a Next.js backend, webhook, or scheduled HTTP call.

---

## When to use this vs. calling Claude directly

| Use orchestrator | Call Claude directly |
|-----------------|---------------------|
| Task has multiple steps | Single question, one-off |
| Input is large raw data | Input is already compact |
| Running the same task repeatedly | Just experimenting |
| You want to track and log runs | You don't need a record |
| You want outputs to improve over time | "Good enough" already |
| Cost matters | Cost isn't a concern |

---

## Flags worth knowing

```bash
--no-eval        # skip optimizer — removes 1–3 extra Ollama calls, use when speed matters
--logs           # show recent run history with token counts and latency
--list           # show all configured pipelines
```

---

## Quick reference

```bash
python runner.py --pipeline trading --ticker AAPL
python runner.py --pipeline trading --ticker AAPL --no-eval
python runner.py --pipeline context_summarizer --path ./myproject
python runner.py --list
python runner.py --logs
python runner.py --logs --pipeline trading -n 50
python runner.py --serve
```

For full technical details — node types, YAML schema, memory system, API endpoints — see **GUIDE.md**.
