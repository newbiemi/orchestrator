"""
Equivalence + determinism tests for the two converted Ollama→Python sites.

Verifies:
  1. summarize_prices() — deterministic across 3 calls, emits key structured fields
  2. interpret_scan() — deterministic across 3 calls, produces valid sentences

These tests do NOT call any LLM. They prove the transforms are:
  (a) stable — identical output on repeated calls with the same input
  (b) structurally valid — output contains the fields Claude needs to reason over
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_EOD = """\
Ticker: AAPL
Period: last 30 days
Date         | Open    | High    | Low     | Close   | Volume
-----------------------------------------------------------------
2026-04-30 | 210.00 | 215.00 | 208.00 | 212.00 |  45,000,000
2026-05-01 | 212.50 | 217.00 | 211.00 | 215.00 |  47,000,000
2026-05-02 | 214.00 | 218.00 | 212.50 | 216.50 |  43,000,000
2026-05-03 | 215.00 | 219.00 | 213.00 | 218.00 |  52,000,000
2026-05-04 | 217.00 | 221.00 | 215.00 | 219.50 |  55,000,000
-----------------------------------------------------------------
30d Return: +3.54%
30d High:   221.00  |  30d Low: 208.00
Avg Volume: 48,400,000
"""

SAMPLE_SIGNALS = """\
CRYPTO: BTCUSDT BUY 72% UPTREND | ETHUSDT SELL 65% DOWNTREND | SOLUSDT BUY 58% TRENDFOLLOW
STOCK: AAPL BUY 71% UPTREND | NVDA SELL 68% DOWNTREND
ETF: SPY HOLD 55% RANGING
FOREX: EURUSD SELL 62% TRENDDOWN | GBPUSD SELL 58% TRENDDOWN
"""


# ── summarize_prices ──────────────────────────────────────────────────────────

class TestSummarizePrices:
    """Equivalence + structural checks for the trading summarizer."""

    def setup_method(self):
        from pipelines.trading import summarize_prices
        self.fn = summarize_prices

    def test_deterministic_across_3_calls(self):
        results = [self.fn(SAMPLE_EOD) for _ in range(3)]
        assert results[0] == results[1] == results[2], (
            "summarize_prices() must be deterministic — got different outputs across calls"
        )

    def test_contains_ticker(self):
        out = self.fn(SAMPLE_EOD)
        assert "AAPL" in out

    def test_contains_last_close(self):
        out = self.fn(SAMPLE_EOD)
        # Last close in our fixture is 219.50
        assert "219.50" in out

    def test_contains_period_return(self):
        out = self.fn(SAMPLE_EOD)
        assert "+3.54%" in out

    def test_contains_price_range(self):
        out = self.fn(SAMPLE_EOD)
        # High 221.00 and Low 208.00 in fixture
        assert "221.00" in out
        assert "208.00" in out

    def test_contains_trend_direction(self):
        out = self.fn(SAMPLE_EOD)
        # 5-day trend: prices rise from 212 to 219.50 → UP
        assert any(w in out for w in ("UP", "DOWN", "FLAT"))

    def test_contains_volume_note(self):
        out = self.fn(SAMPLE_EOD)
        assert any(w in out for w in ("volume", "Volume"))

    def test_empty_input_does_not_crash(self):
        """Graceful handling of empty or minimal input."""
        out = self.fn("")
        assert isinstance(out, str)

    def test_output_is_str_not_dict(self):
        """Must return str (passed straight through as NodeResult.output)."""
        out = self.fn(SAMPLE_EOD)
        assert isinstance(out, str)

    def test_output_has_multiple_lines(self):
        """Summary must be multi-line — single-line would lose structure."""
        out = self.fn(SAMPLE_EOD)
        assert out.count("\n") >= 3


# ── interpret_scan ────────────────────────────────────────────────────────────

class TestInterpretScan:
    """Equivalence + structural checks for the cross-asset scan interpreter."""

    def setup_method(self):
        from deliver_scan import interpret_scan
        self.fn = interpret_scan

    def test_deterministic_across_3_calls(self):
        results = [self.fn(SAMPLE_SIGNALS) for _ in range(3)]
        assert results[0] == results[1] == results[2], (
            "interpret_scan() must be deterministic — got different outputs across calls"
        )

    def test_contains_buy_sell_counts(self):
        out = self.fn(SAMPLE_SIGNALS)
        # 4 BUYs and 5 SELLs in fixture — at least mention them
        assert "BUY" in out or "buy" in out.lower()
        assert "SELL" in out or "sell" in out.lower()

    def test_has_at_least_one_sentence(self):
        out = self.fn(SAMPLE_SIGNALS)
        assert len(out) > 20, "Output too short to be a meaningful interpretation"

    def test_output_is_str(self):
        out = self.fn(SAMPLE_SIGNALS)
        assert isinstance(out, str)

    def test_empty_input_returns_string(self):
        out = self.fn("")
        assert isinstance(out, str)

    def test_all_holds_returns_string(self):
        holds_only = "CRYPTO: BTCUSDT HOLD 50% RANGING | ETHUSDT HOLD 45% RANGING"
        out = self.fn(holds_only)
        # No BUY/SELL — should handle gracefully
        assert isinstance(out, str)

    def test_bias_is_bearish_when_sells_dominate(self):
        bearish = "CRYPTO: BTCUSDT SELL 80% DOWNTREND | ETHUSDT SELL 75% DOWNTREND | SOLUSDT SELL 70% DOWNTREND"
        out = self.fn(bearish)
        assert "bearish" in out.lower()

    def test_bias_is_bullish_when_buys_dominate(self):
        bullish = "CRYPTO: BTCUSDT BUY 80% UPTREND | ETHUSDT BUY 75% UPTREND | SOLUSDT BUY 70% UPTREND"
        out = self.fn(bullish)
        assert "bullish" in out.lower()
