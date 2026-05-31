"""
Trading pipeline — Phase 3

Flow:
  1. Fetch EOD price data via yfinance
  2. PythonNode (summarize_prices) — deterministic template summary (no Ollama)
  3. ClaudeNode (model from pipelines.yaml defaults) — BUY / HOLD / SELL decision
  4. Log everything to SQLite (via core/logger.py)
  5. Optional Alpaca execution flag (ALPACA_EXECUTE=true in .env)

CLI:
  python runner.py --pipeline trading --ticker AAPL
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import yfinance as yf
from dotenv import load_dotenv
from rich.console import Console

from core.pipeline import Pipeline
from core.claude_node import ClaudeNode
from core.python_node import PythonNode
from core.config import load_pipeline_config, get_default_claude_model

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


# ── Deterministic price summarizer (replaces OllamaNode) ─────────────────────

_ROW_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d,]+)"
)
_RETURN_RE  = re.compile(r"30d Return:\s*([+\-][\d.]+)%")
_RANGE_RE   = re.compile(r"30d High:\s*([\d.]+)\s*\|\s*30d Low:\s*([\d.]+)")
_AVGVOL_RE  = re.compile(r"Avg Volume:\s*([\d,]+)")


def summarize_prices(input_text: str) -> str:
    """
    Deterministic template summary of the OHLCV text from fetch_eod_data().
    Returns a compact structured block Claude can directly reason over.

    This replaces the Ollama summarizer node — identical information content,
    zero variance across runs (algorithm-first, per projectbrief.md).
    """
    lines = input_text.splitlines()

    # Extract header fields
    ticker     = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Ticker:")), "?")
    period     = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Period:")),  "?")
    ret_m      = next((_RETURN_RE.search(l) for l in lines if _RETURN_RE.search(l)),  None)
    range_m    = next((_RANGE_RE.search(l)  for l in lines if _RANGE_RE.search(l)),   None)
    avgvol_m   = next((_AVGVOL_RE.search(l) for l in lines if _AVGVOL_RE.search(l)),  None)

    ret_str    = f"{ret_m.group(1)}%" if ret_m else "N/A"
    high_str   = range_m.group(1) if range_m else "N/A"
    low_str    = range_m.group(2) if range_m else "N/A"
    avgvol_str = avgvol_m.group(1) if avgvol_m else "N/A"

    # Parse individual OHLCV rows
    rows = [_ROW_RE.match(l) for l in lines]
    rows = [r for r in rows if r]
    closes = [float(r.group(5)) for r in rows]
    dates  = [r.group(1) for r in rows]
    vols   = [int(r.group(6).replace(",", "")) for r in rows]

    # Recent 5-day trend
    if len(closes) >= 5:
        c_first, c_last = closes[-5], closes[-1]
        d_first, d_last = dates[-5], dates[-1]
        pct_5d = ((c_last / c_first) - 1) * 100
        direction = "UP" if pct_5d > 0.5 else ("DOWN" if pct_5d < -0.5 else "FLAT")
        trend_str = (
            f"{direction} — {d_first}→{d_last}  "
            f"${c_first:.2f}→${c_last:.2f} ({pct_5d:+.1f}%)"
        )
        recent_closes = "  ".join(f"${c:.2f}" for c in closes[-5:])
    else:
        trend_str = "N/A (insufficient data)"
        recent_closes = "  ".join(f"${c:.2f}" for c in closes[-5:])

    # Volume context (last session vs average)
    if closes and vols:
        avg_vol = sum(vols) / len(vols)
        last_vol = vols[-1]
        vol_ratio = last_vol / avg_vol if avg_vol else 1.0
        vol_note = (
            "above-average volume" if vol_ratio > 1.2
            else ("below-average volume" if vol_ratio < 0.8 else "average volume")
        )
    else:
        vol_note = "N/A"

    if not closes:
        return f"PRICE SUMMARY — {ticker} ({period})\nNo price rows parsed from input."

    summary = (
        f"PRICE SUMMARY — {ticker} ({period})\n"
        f"Last Close: ${closes[-1]:.2f}  |  {period} Return: {ret_str}\n"
        f"Price Range: ${low_str}–${high_str}  |  Avg Vol: {avgvol_str}\n"
        f"\n"
        f"5-Day Trend: {trend_str}\n"
        f"Recent closes (5d): {recent_closes}\n"
        f"Last session: {vol_note}\n"
    )
    return summary


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

        summarizer = PythonNode(fn="pipelines.trading.summarize_prices")

        decision = ClaudeNode(
            model=decision_cfg.get("model") or get_default_claude_model(),
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
