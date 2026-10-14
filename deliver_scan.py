"""
deliver_scan.py — run multi-asset scans and deliver a sectioned card digest to Telegram.

Asset classes: CRYPTO (10) · STOCK (7) · ETF (7) · FOREX (all)
Scheduled via Windows Task Scheduler: 09:00 + 21:00 WIB.

Signal card format (BUY/SELL only — HOLDs shown as a compact list):

    🟢 SELL · XAUTUSDT
    $4,543.19 | 27% conf | RSI 52.5 | Score -2.20
    Regime: TRENDDOWN | Strategy: trendfollow

Requires env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BOT_SCRIPT
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# Load .env from orchestrator root (simple parser — no dependency on python-dotenv)
_ENV_FILE = os.path.join(_HERE, ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BOT_SCRIPT = os.path.expandvars(os.environ.get("BOT_SCRIPT", ""))
MAX_MSG_LEN = 4096

ASSET_CLASSES = [
    ("CRYPTO", 10, "🪙"),
    ("STOCK",   7, "📈"),
    ("ETF",     7, "💼"),
    ("FOREX",  12, "💱"),
]

SIGNAL_EMOJI = {"SELL": "🔴", "BUY": "🟢", "HOLD": "⚫"}
SIGNAL_ORDER = {"SELL": 0, "BUY": 1, "HOLD": 2}

_DETAIL_RE = re.compile(
    r'\b([A-Z][A-Z0-9\-\.]{1,19}(?:=X)?)'   # symbol: uppercase, optional =X (forex)
    r'\s+\$\s*([\d,\.]+)'                     # $ price
    r'\s*\|\s*(BUY|SELL|HOLD)'                # signal
    r'\s*\(\s*(\d+)%\)'                       # (confidence%)
    r'(?:\s*\|\s*RSI:\s*([\d\.]+))?'          # RSI (optional)
    r'(?:\s*\|\s*Score:\s*([\+\-]?\d+\.?\d*))?',  # Score (optional)
)
_REGIME_RE = re.compile(r'Regime:\s*(\w+)(?:\s*\|\s*Strategy:\s*(\w+))?', re.IGNORECASE)
_UESC_RE = re.compile(r'\\[uU]([0-9a-fA-F]{4,8})')


# ── Parsing ────────────────────────────────────────────────────────────────────

def _decode_escapes(text: str) -> str:
    def _r(m):
        try:
            return chr(int(m.group(1), 16))
        except (ValueError, OverflowError):
            return m.group(0)
    return _UESC_RE.sub(_r, text)


def parse_signals(raw: str) -> list[dict]:
    raw = _decode_escapes(raw)
    signals, lines = [], raw.splitlines()
    i = 0
    while i < len(lines):
        m = _DETAIL_RE.search(lines[i])
        if m:
            sym, price, signal, conf, rsi, score = m.groups()
            regime, strategy = "", ""
            if i + 1 < len(lines):
                rm = _REGIME_RE.search(lines[i + 1])
                if rm:
                    regime = (rm.group(1) or "").upper()
                    strategy = (rm.group(2) or "").lower()
                    i += 1
            signals.append({
                "symbol":     sym.strip(),
                "price":      float(price.replace(",", "")),
                "signal":     signal.upper(),
                "confidence": int(conf),
                "rsi":        float(rsi) if rsi else None,
                "score":      float(score) if score else None,
                "regime":     regime,
                "strategy":   strategy,
            })
        i += 1
    signals.sort(key=lambda s: (SIGNAL_ORDER.get(s["signal"], 9), -s["confidence"]))
    return signals


# ── Card + section builder ─────────────────────────────────────────────────────

def _sym_display(sym: str, asset_class: str) -> str:
    if asset_class == "CRYPTO":
        return sym.replace("USDT", "").replace("BTC", "")
    if asset_class == "FOREX":
        return sym.replace("=X", "")
    return sym


def _signal_card(s: dict, asset_class: str) -> str:
    emoji = SIGNAL_EMOJI.get(s["signal"], "")
    sym = _sym_display(s["symbol"], asset_class)
    price_str = f"${s['price']:,.2f}"
    rsi_str = f"RSI: {s['rsi']:.2f}" if s["rsi"] is not None else ""
    score_str = f"Score: {s['score']:+.2f}" if s["score"] is not None else ""
    detail_parts = [p for p in [f"{s['confidence']}% conf", rsi_str, score_str] if p]
    regime_parts = [p for p in [f"Regime: {s['regime']}" if s["regime"] else "",
                                 f"Strategy: {s['strategy']}" if s["strategy"] else ""] if p]
    lines = [
        f"{emoji} *{s['signal']} · {sym}*",
        f"{price_str} | {' | '.join(detail_parts)}",
    ]
    if regime_parts:
        lines.append(" | ".join(regime_parts))
    return "\n".join(lines)


def _section(icon: str, asset_class: str, signals: list[dict], error: str = "") -> str:
    lines = [f"{icon} *{asset_class}*"]
    if error:
        lines.append(f"_{error}_")
        return "\n".join(lines)
    if not signals:
        lines.append("_no signals_")
        return "\n".join(lines)

    actionable = [s for s in signals if s["signal"] in ("BUY", "SELL")]
    holds = [s for s in signals if s["signal"] == "HOLD"]

    # Summary stats
    c = Counter(s["signal"] for s in signals)
    stat_parts = [f"{SIGNAL_EMOJI[sig]} {sig}:{c[sig]}" for sig in ("SELL", "BUY", "HOLD") if c.get(sig)]
    lines.append("  ".join(stat_parts))

    # Full card for each actionable signal
    if actionable:
        lines.append("")
        for s in actionable:
            lines.append(_signal_card(s, asset_class))
            lines.append("")

    # Compact HOLD list
    if holds:
        hold_syms = ", ".join(_sym_display(s["symbol"], asset_class) for s in holds)
        lines.append(f"⚫ Hold: {hold_syms}")

    return "\n".join(lines)


# ── Deterministic cross-asset interpretation (replaces Ollama) ─────────────────

_SIGNAL_PARSE_RE = re.compile(r"\b(BUY|SELL|HOLD)\b")
_REGIME_WORD_RE  = re.compile(r"\b(UPTREND|DOWNTREND|TRENDDOWN|TRENDFOLLOW|RANGING|VOLATILE)\b")


def interpret_scan(input_text: str) -> str:
    """
    Deterministic 2–3 sentence cross-asset digest from the signal summary text.
    Format per line: "ASSET_CLASS: SYM SIGNAL CONF% REGIME | ..."

    This replaces the llama3.2:3b Ollama call — identical structural information,
    zero variance, no local model dependency (algorithm-first).

    Also registered as fn= for the trading_scan_daily.market_summary YAML node.
    """
    if not input_text.strip():
        return ""

    lines = [l.strip() for l in input_text.splitlines() if l.strip()]

    # Tally signals and regimes per asset class
    class_tallies: dict[str, dict] = {}
    all_buys, all_sells = 0, 0
    strongest_class, strongest_count = "", 0
    highest_conf_sig: dict = {}

    for line in lines:
        parts = line.split(":", 1)
        if len(parts) < 2:
            continue
        asset_class = parts[0].strip()
        body = parts[1]

        signals_in_line = _SIGNAL_PARSE_RE.findall(body)
        regimes_in_line = _REGIME_WORD_RE.findall(body)
        buys  = signals_in_line.count("BUY")
        sells = signals_in_line.count("SELL")
        all_buys  += buys
        all_sells += sells

        # Extract confidence values (digits before %)
        confs = [int(c) for c in re.findall(r"(\d+)%", body)]
        max_conf = max(confs) if confs else 0

        class_tallies[asset_class] = {
            "buys": buys, "sells": sells,
            "regime": regimes_in_line[0] if regimes_in_line else "",
            "max_conf": max_conf,
        }

        actionable = buys + sells
        if actionable > strongest_count:
            strongest_count = actionable
            strongest_class = asset_class

        if max_conf > highest_conf_sig.get("conf", 0):
            # Find the symbol with this confidence
            sym_m = re.search(r"\b([A-Z]{2,10}(?:USDT|=X)?)\s+(?:BUY|SELL|HOLD)\s+" + str(max_conf) + r"%", body)
            sym = sym_m.group(1) if sym_m else ""
            sigs = _SIGNAL_PARSE_RE.findall(body[:body.find(str(max_conf))+5]) if body.find(str(max_conf)) >= 0 else []
            highest_conf_sig = {"conf": max_conf, "sym": sym, "signal": sigs[-1] if sigs else ""}

    total = all_buys + all_sells
    if total == 0:
        return "No actionable signals detected across asset classes in this scan."

    # Sentence 1 — overall bias
    if all_buys > all_sells * 1.5:
        bias = "broadly bullish"
    elif all_sells > all_buys * 1.5:
        bias = "broadly bearish"
    else:
        bias = "mixed"
    sentence1 = f"Overall market posture is {bias} with {all_buys} BUY and {all_sells} SELL signals across all asset classes."

    # Sentence 2 — dominant class + regime
    if strongest_class:
        t = class_tallies[strongest_class]
        regime_note = f" ({t['regime'].lower().replace('_', ' ')})" if t["regime"] else ""
        action_bias = "bullish" if t["buys"] > t["sells"] else ("bearish" if t["sells"] > t["buys"] else "neutral")
        sentence2 = f"{strongest_class} is the most active class{regime_note}, showing a {action_bias} bias with {t['buys']} buy(s) and {t['sells']} sell(s)."
    else:
        sentence2 = ""

    # Sentence 3 — highest-confidence signal
    if highest_conf_sig.get("sym"):
        h = highest_conf_sig
        sentence3 = f"Highest-confidence signal: {h['sym']} {h['signal']} at {h['conf']}% — watch this as the lead indicator."
    else:
        sentence3 = ""

    parts = [s for s in [sentence1, sentence2, sentence3] if s]
    return " ".join(parts)


# ── Bot runner ─────────────────────────────────────────────────────────────────

def _run_scan(asset_class: str, top_n: int) -> tuple[list[dict], str]:
    if not BOT_SCRIPT:
        return [], "BOT_SCRIPT env not set"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, BOT_SCRIPT, "--once",
           "--asset-class", asset_class, "--top-n", str(top_n)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace", env=env,
        )
        raw = proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")
        return parse_signals(raw), ""
    except subprocess.TimeoutExpired:
        return [], "scan timed out after 180s"
    except Exception as exc:
        return [], str(exc)


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[deliver_scan] credentials missing")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks = [text[i:i + MAX_MSG_LEN] for i in range(0, len(text), MAX_MSG_LEN)]
    for chunk in chunks:
        for parse_mode in ("Markdown", ""):
            payload: dict = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            data = urllib.parse.urlencode(payload).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                    if resp.get("ok"):
                        break
                    if parse_mode:
                        print(f"[deliver_scan] Markdown error, retrying plain")
                    else:
                        print(f"[deliver_scan] Telegram error: {resp}")
            except Exception:
                if not parse_mode:
                    raise


# ── Main ───────────────────────────────────────────────────────────────────────

def run_once(classes: list[str] | None = None, top_n: int | None = None,
              send: bool = True) -> str:
    """
    Run one multi-asset scan cycle and return the built digest message.

    classes: subset of asset class names to scan (default: all of ASSET_CLASSES).
    top_n:   override symbol count for every scanned class (default: each class's
             own default count from ASSET_CLASSES).
    send:    push the message to Telegram (default True — matches legacy behavior).

    Shared by the scheduled-task entry point (main(), below) and
    bots/scan_controller.py, so both paths build an identical digest.
    """
    now = datetime.now()
    slot = "AM" if now.hour < 15 else "PM"
    timestamp = now.strftime("%d %b %Y %H:%M WIB")
    print(f"[deliver_scan] {now.isoformat()} slot={slot}")

    active = [(ac, n, icon) for ac, n, icon in ASSET_CLASSES if not classes or ac in classes]
    if not active:
        active = ASSET_CLASSES

    sections = []
    llm_context_lines = []

    for asset_class, default_n, icon in active:
        n = top_n if top_n is not None else default_n
        print(f"[deliver_scan] scanning {asset_class}…")
        signals, error = _run_scan(asset_class, n)
        sections.append(_section(icon, asset_class, signals, error))

        # Top signals for LLM context (actionable only)
        actionable = [s for s in signals if s["signal"] in ("BUY", "SELL")][:3]
        if actionable:
            rows = [f"{s['symbol']} {s['signal']} {s['confidence']}% {s['regime']}" for s in actionable]
            llm_context_lines.append(f"{asset_class}: " + " | ".join(rows))
        print(f"[deliver_scan] {asset_class}: {len(signals)} signals, error={error or 'none'}")

    interpretation = interpret_scan("\n".join(llm_context_lines)) if llm_context_lines else ""

    msg_parts = [f"*Multi-Asset Scan — {slot} ({timestamp})*", ""]
    for i, section in enumerate(sections):
        msg_parts.append(section)
        if i < len(sections) - 1:
            msg_parts.append("")

    if interpretation:
        msg_parts += ["", "💬 *Cross-Asset Digest*", interpretation]

    msg = "\n".join(msg_parts)
    if send:
        send_telegram(msg)
        print(f"[deliver_scan] delivered {len(msg)} chars")
    return msg


def main():
    run_once()


if __name__ == "__main__":
    main()
