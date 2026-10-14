"""
Ollama node — sync httpx calls to localhost:11434.
Safe for CLI, cron, and FastAPI (both def and async def routes).
"""
import os
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

from .logger import log_run
from .claude_node import NodeResult

load_dotenv()

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = "qwen2.5-coder"
REQUEST_TIMEOUT = 90.0  # seconds — fail fast; 300s masked VRAM-swap stalls

# ── Circuit breaker ────────────────────────────────────────────────────────────
# After CB_THRESHOLD consecutive failures, fail fast for CB_RESET_SECS seconds
# instead of hanging for the full REQUEST_TIMEOUT each time.
_cb_failures: int = 0
_cb_last_fail: float = 0.0
CB_THRESHOLD   = 3
CB_RESET_SECS  = 60.0


def _cb_check() -> None:
    if _cb_failures >= CB_THRESHOLD:
        elapsed = time.time() - _cb_last_fail
        if elapsed < CB_RESET_SECS:
            wait = int(CB_RESET_SECS - elapsed)
            raise RuntimeError(
                f"Ollama circuit open ({_cb_failures} consecutive failures, "
                f"last {int(elapsed)}s ago). Retry in {wait}s or restart Ollama."
            )


def _cb_success() -> None:
    global _cb_failures, _cb_last_fail
    _cb_failures = 0
    _cb_last_fail = 0.0


def _cb_failure() -> None:
    global _cb_failures, _cb_last_fail
    _cb_failures += 1
    _cb_last_fail = time.time()


class OllamaNode:
    """
    A single Ollama inference call.

    Usage:
        node = OllamaNode(model="qwen2.5-coder", system="You summarize price data.")
        result = node.run(pipeline="trading", node="summarizer", input_text="AAPL data...")
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.2,
        prompt_template: Optional[str] = None,
    ):
        self.model = model
        self.system = system
        self.temperature = temperature
        self.prompt_template = prompt_template

    def _build_prompt(self, input_text: str) -> str:
        if self.prompt_template:
            return self.prompt_template.replace("{input}", input_text)
        return input_text

    def run(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        _cb_check()  # fail fast if Ollama has been consistently down

        prompt = self._build_prompt(input_text)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        t0 = time.perf_counter()
        _last_transient: Optional[Exception] = None

        for attempt in range(2):  # 1 try + 1 retry on timeout/connect failure
            if attempt:
                time.sleep(2)
            try:
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
                    resp.raise_for_status()
                    data = resp.json()

                output = data["message"]["content"]
                tokens = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
                latency = int((time.perf_counter() - t0) * 1000)

                _cb_success()
                log_run(
                    pipeline=pipeline,
                    node=node,
                    model=self.model,
                    input_text=input_text,
                    output_text=output,
                    latency_ms=latency,
                    token_count=tokens,
                )
                return NodeResult(
                    output=output,
                    model=self.model,
                    token_count=tokens,
                    latency_ms=latency,
                    pipeline=pipeline,
                    node=node,
                )
            except (httpx.ReadTimeout, httpx.ConnectError) as exc:
                _last_transient = exc
                continue  # retry once
            except Exception as exc:
                _cb_failure()
                latency = int((time.perf_counter() - t0) * 1000)
                log_run(
                    pipeline=pipeline,
                    node=node,
                    model=self.model,
                    input_text=input_text,
                    output_text="",
                    latency_ms=latency,
                    token_count=0,
                    status="error",
                    error=str(exc),
                )
                raise

        # Both attempts timed out / connection failed
        _cb_failure()
        latency = int((time.perf_counter() - t0) * 1000)
        err_msg = (
            f"Ollama not responding at {OLLAMA_BASE_URL} after 2 attempts "
            f"({REQUEST_TIMEOUT}s each). Run 'ollama ps' and 'ollama serve'."
        )
        log_run(
            pipeline=pipeline,
            node=node,
            model=self.model,
            input_text=input_text,
            output_text="",
            latency_ms=latency,
            token_count=0,
            status="error",
            error=err_msg,
        )
        raise RuntimeError(err_msg) from _last_transient
