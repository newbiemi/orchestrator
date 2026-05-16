"""
Trading pipeline — Phase 3

Flow:
  1. Fetch EOD price data via yfinance
  2. OllamaNode (qwen2.5-coder) — summarize price action
  3. ClaudeNode (claude-sonnet-4-5) — BUY / HOLD / SELL decision
  4. Log everything to SQLite (via core/logger.py)
  5. Optional Alpaca execution flag (ALPACA_EXECUTE=true in .env)

CLI:
  python runner.py --pipeline trading --ticker AAPL
"""
import json
import os
from datetime import datetime, timedelta, timezone

import yfinance as yf
from dotenv import load_dotenv
from rich.console import Console

from core.pipeline import Pipeline
from core.claude_node import ClaudeNode
from core.ollama_node import OllamaNode
from core.config import load_pipeline_config

load_dotenv()
console = Console(legacy_windows=False)

# ── Decision parser ────────────────────────────────────────────────────────────

VALID_DECISIONS = {"BUY", "HOLD", "SELL"}


def parse_decision(text: str) -> dict:
    """
    Extract structured decision from Claude output.
    Expected format: DECISION: BUY — reason text

    Looks for the decision keyword on the DECISION: line first to avoid
    matching decision words that appear in the reason text.
    """
    import re
    decision = "UNKNOWN"
    reason = text

    # Primary: match "DECISION: BUY/HOLD/SELL" on its own line or at start
    m = re.search(r"DECISION\s*[:\-]\s*(BUY|HOLD|SELL)", text, re.IGNORECASE)
    if m:
        decision = m.group(1).upper()
        # Extract reason — everything after the em-dash or after the decision word
        after = text[m.end():].strip()
        reason = after.lstrip("—–- ").strip() or text
    else:
        # Fallback: scan only the first line for the keyword
        first_line = text.split("\n")[0].upper()
        for d in VALID_DECISIONS:
            if d in first_line:
                decision = d
                break

    return {"decision": decision, "reason": reason, "raw": text}


# ── Data fetcher ───────────────────────────────────────────────────────────────

def fetch_eod_data(ticker: str, days: int = 30) -> str:
    """
    Fetch last N days of EOD OHLCV data for ticker.
    Returns a formatted string ready for the summarizer node.
    """
    console.print(f"  [dim]Fetching {days}d EOD data for {ticker}...[/dim]")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    tk = yf.Ticker(ticker)
    hist = tk.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

    if hist.empty:
        raise ValueError(f"No data returned for ticker '{ticker}'. Check the symbol.")

    # Format as a compact text table
    lines = [f"Ticker: {ticker}", f"Period: last {days} days", "Date         | Open    | High    | Low     | Close   | Volume"]
    lines.append("-" * 65)
    for date, row in hist.iterrows():
        lines.append(
            f"{date.strftime('%Y-%m-%d')} | {row['Open']:7.2f} | {row['High']:7.2f} | "
            f"{row['Low']:7.2f} | {row['Close']:7.2f} | {int(row['Volume']):>10,}"
        )

    # Add summary stats
    closes = hist["Close"]
    lines.append("-" * 65)
    lines.append(f"30d Return: {((closes.iloc[-1] / closes.iloc[0]) - 1) * 100:+.2f}%")
    lines.append(f"30d High:   {hist['High'].max():.2f}  |  30d Low: {hist['Low'].min():.2f}")
    lines.append(f"Avg Volume: {int(hist['Volume'].mean()):,}")

    return "\n".join(lines)


# ── Alpaca execution (optional) ────────────────────────────────────────────────

def maybe_execute_alpaca(ticker: str, decision: str) -> None:
    """
    If ALPACA_EXECUTE=true and decision is BUY/SELL, log intent.
    Actual order submission requires alpaca-trade-api — stubbed here.
    """
    if os.environ.get("ALPACA_EXECUTE", "").lower() != "true":
        return
    if decision not in ("BUY", "SELL"):
        return

    console.print(
        f"  [bold yellow]ALPACA EXECUTE flag is set — would submit {decision} order for {ticker}[/bold yellow]"
    )
    console.print("  [dim](Alpaca order execution is stubbed — install alpaca-trade-api to activate)[/dim]")


# ── Pipeline class ─────────────────────────────────────────────────────────────

class TradingPipeline(Pipeline):
    name = "trading"

    def build_nodes(self, **kwargs) -> list[tuple[str, ClaudeNode | OllamaNode]]:
        cfg = load_pipeline_config(self.name)
        nodes_cfg = {n["name"]: n for n in cfg.get("nodes", [])}

        summarizer_cfg = nodes_cfg.get("summarizer", {})
        decision_cfg   = nodes_cfg.get("decision", {})

        summarizer = OllamaNode(
            model=summarizer_cfg.get("model", "qwen2.5-coder"),
            system=summarizer_cfg.get("system", "You summarize stock price data concisely."),
            prompt_template=summarizer_cfg.get("prompt_template"),
            temperature=float(summarizer_cfg.get("temperature", 0.1)),
        )

        decision = ClaudeNode(
            model=decision_cfg.get("model", "claude-sonnet-4-5"),
            system=decision_cfg.get("system", "You are a trading analyst. Reply BUY, HOLD, or SELL with a reason."),
            prompt_template=decision_cfg.get("prompt_template"),
            max_tokens=int(decision_cfg.get("max_tokens", 256)),
            temperature=float(decision_cfg.get("temperature", 0.2)),
        )

        return [
            ("summarizer", summarizer),
            ("decision",   decision),
        ]

    def run(self, initial_input: str = "AAPL", **kwargs) -> None:  # type: ignore[override]
        ticker = kwargs.get("ticker", initial_input).upper()

        try:
            price_data = fetch_eod_data(ticker)
        except Exception as exc:
            console.print(f"[red]Data fetch failed: {exc}[/red]")
            return

        result = super().run(initial_input=price_data, **kwargs)

        if result.status == "ok":
            parsed = parse_decision(result.final_output)
            console.print(f"\n[bold green]Decision:[/bold green] {parsed['decision']}")
            console.print(f"[dim]Reason: {parsed['reason']}[/dim]")
            maybe_execute_alpaca(ticker, parsed["decision"])

        return result
