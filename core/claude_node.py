"""
Claude node — wraps the Anthropic SDK.
Each call returns a NodeResult with output text + metadata.

If ANTHROPIC_API_KEY is missing/empty at call time, automatically falls back
to Ollama using OLLAMA_FALLBACK_MODEL (default: qwen2.5-coder).
The fallback is transparent — NodeResult format is identical; model field
records "<ollama-model> [claude-fallback]" so logs show what actually ran.
"""
import os
import time
from dataclasses import dataclass
from typing import Optional

import anthropic
from dotenv import load_dotenv
from rich.console import Console

from .logger import log_run

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 1024
FALLBACK_MODEL_ENV = "OLLAMA_FALLBACK_MODEL"
FALLBACK_MODEL_DEFAULT = "qwen2.5-coder"

_console = Console(legacy_windows=False)


@dataclass
class NodeResult:
    output: str
    model: str
    token_count: int
    latency_ms: int
    pipeline: str = ""
    node: str = ""


class ClaudeNode:
    """
    A single Claude API call, configurable per-instance.

    Usage:
        node = ClaudeNode(model="claude-sonnet-4-5", system="You are a trader.")
        result = node.run(pipeline="trading", node="decision", input_text="Price data...")

    If ANTHROPIC_API_KEY is absent, falls back to Ollama automatically.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        system: str = "You are a helpful assistant.",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
        prompt_template: Optional[str] = None,
    ):
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_template = prompt_template  # "{input}" replaced at runtime
        # Client is built lazily in run() so missing key doesn't crash __init__

    def _build_prompt(self, input_text: str) -> str:
        if self.prompt_template:
            return self.prompt_template.replace("{input}", input_text)
        return input_text

    def _run_fallback(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        """Delegate to Ollama when Claude API key is unavailable."""
        # Lazy import avoids circular dependency (ollama_node imports NodeResult from here)
        from .ollama_node import OllamaNode

        fallback_model = os.environ.get(FALLBACK_MODEL_ENV, FALLBACK_MODEL_DEFAULT)
        _console.print(
            f"  [yellow]⚠ ANTHROPIC_API_KEY missing — falling back to "
            f"Ollama ({fallback_model}) for node '{node}'[/yellow]"
        )
        ollama = OllamaNode(
            model=fallback_model,
            system=self.system,
            temperature=self.temperature,
            prompt_template=self.prompt_template,
        )
        result = ollama.run(pipeline=pipeline, node=node, input_text=input_text)
        result.model = f"{fallback_model} [claude-fallback]"
        return result

    def run(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return self._run_fallback(pipeline=pipeline, node=node, input_text=input_text)

        prompt = self._build_prompt(input_text)
        t0 = time.perf_counter()
        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.content[0].text
            tokens = response.usage.input_tokens + response.usage.output_tokens
            latency = int((time.perf_counter() - t0) * 1000)
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
        except Exception as exc:
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
