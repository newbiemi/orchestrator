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
from .config import get_default_claude_model

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-6"  # fallback if config.py unreachable
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
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None


class ClaudeNode:
    """
    A single Claude API call, configurable per-instance.

    Usage:
        node = ClaudeNode(system="You are a trader.")  # model from pipelines.yaml defaults
        result = node.run(pipeline="trading", node="decision", input_text="Price data...")

    If ANTHROPIC_API_KEY is absent, falls back to Ollama automatically.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        system: str = "You are a helpful assistant.",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
        prompt_template: Optional[str] = None,
    ):
        self.model = model or get_default_claude_model()
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_template = prompt_template  # "{input}" replaced at runtime
        self._client: Optional[anthropic.Anthropic] = None  # built lazily on first real call
        self.memory_context: Optional[str] = None  # set by pipeline._inject_memory_context

    def _build_prompt(self, input_text: str) -> str:
        if self.prompt_template:
            return self.prompt_template.replace("{input}", input_text)
        return input_text

    def _run_fallback(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        """Delegate to a free backend when the Claude API key is unavailable.

        Tries the hosted free APIs first (Groq, Gemini — via llmroute), then
        falls through to OllamaNode. Ollama stays a real OllamaNode rather than
        an llmroute backend so it keeps its circuit breaker: after 3 consecutive
        failures it fails fast for 60s instead of waiting out the 90s timeout
        each time. See CLAUDE.md on the VRAM stall this protects against.
        """
        # Lazy import avoids circular dependency (ollama_node imports NodeResult from here)
        from .ollama_node import OllamaNode

        hosted = self._try_hosted_free(pipeline=pipeline, node=node, input_text=input_text)
        if hosted is not None:
            return hosted

        fallback_model = os.environ.get(FALLBACK_MODEL_ENV, FALLBACK_MODEL_DEFAULT)
        _console.print(
            f"  [yellow]⚠ ANTHROPIC_API_KEY missing — falling back to "
            f"Ollama ({fallback_model}) for node '{node}'[/yellow]"
        )
        # Pass combined system string to Ollama — it has no concept of block caching
        system_str = self.system
        if self.memory_context:
            system_str = f"{self.system}\n\n{self.memory_context}"
        ollama = OllamaNode(
            model=fallback_model,
            system=system_str,
            temperature=self.temperature,
            prompt_template=self.prompt_template,
        )
        result = ollama.run(pipeline=pipeline, node=node, input_text=input_text)
        result.model = f"{fallback_model} [claude-fallback]"
        return result

    def _try_hosted_free(self, *, pipeline: str, node: str, input_text: str) -> Optional[NodeResult]:
        """Attempt Groq/Gemini via llmroute. Returns None so the caller can fall
        through to Ollama — a missing key or a rate limit is not an error here,
        it just means the next backend gets a turn."""
        try:
            from llmroute import env, policy, telemetry
            from llmroute.cache import flatten, system_blocks
        except ImportError:
            return None

        try:
            env.ensure_loaded()
            available = [b for b in env.available_backends() if b != "ollama"]
            if not available:
                return None

            system = flatten(system_blocks(self.system, self.memory_context, force=True))
            t0 = time.perf_counter()
            result = policy.call(self._build_prompt(input_text), "compress", system=system)
            latency = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            _console.print(f"  [dim]free API chain unavailable ({exc}) — trying Ollama[/dim]")
            return None

        model_label = f"{result.backend} [claude-fallback]"
        _console.print(
            f"  [yellow]! ANTHROPIC_API_KEY missing — answered by "
            f"{result.backend} for node '{node}'[/yellow]"
        )
        log_run(
            pipeline=pipeline,
            node=node,
            model=model_label,
            input_text=input_text,
            output_text=result.text,
            latency_ms=latency,
            token_count=0,
        )
        try:
            telemetry.record(
                backend=result.backend, outcome="ok", task_type="compress",
                latency_ms=latency, chars=len(result.text), project=pipeline, node=node,
            )
        except Exception:
            pass
        return NodeResult(
            output=result.text, model=model_label, token_count=0,
            latency_ms=latency, pipeline=pipeline, node=node,
        )

    def run(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return self._run_fallback(pipeline=pipeline, node=node, input_text=input_text)

        # Lazy client construction — reuse across calls on the same instance
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=api_key)

        prompt = self._build_prompt(input_text)

        # Two-block system list so the cache breakpoint sits between stable base
        # prompt and the volatile memory block injected per-run.
        system_blocks: list[dict] = [
            {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}
        ]
        if self.memory_context:
            system_blocks.append({"type": "text", "text": self.memory_context})

        t0 = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": prompt}],
            )
            output = response.content[0].text
            tokens = response.usage.input_tokens + response.usage.output_tokens
            cache_write = getattr(response.usage, "cache_creation_input_tokens", None)
            cache_read  = getattr(response.usage, "cache_read_input_tokens", None)
            latency = int((time.perf_counter() - t0) * 1000)
            log_run(
                pipeline=pipeline,
                node=node,
                model=self.model,
                input_text=input_text,
                output_text=output,
                latency_ms=latency,
                token_count=tokens,
                cache_creation_input_tokens=cache_write,
                cache_read_input_tokens=cache_read,
            )
            return NodeResult(
                output=output,
                model=self.model,
                token_count=tokens,
                latency_ms=latency,
                pipeline=pipeline,
                node=node,
                cache_creation_input_tokens=cache_write,
                cache_read_input_tokens=cache_read,
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
