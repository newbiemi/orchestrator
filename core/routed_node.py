"""
Routed node — cost-first execution with an algorithmic quality gate.

Wraps a ClaudeNode so a step can be attempted on the free chain (Groq → Gemini →
Ollama, via llmroute) and only escalated to Claude when the cheap answer fails a
gate. Selected per node in pipelines.yaml:

    tier: free              # free chain only, never escalates
    tier: free-then-verify  # free chain, gated, escalate on failure
    tier: claude            # default — unchanged ClaudeNode behaviour

The gate is deliberately algorithmic (length, JSON shape, required keys, refusal
patterns). An LLM-scored gate would spend the tokens this node exists to save.

Escalation is not free: a failing gate costs a free call *plus* a Claude call.
`llmroute.telemetry` records `escalated` per run so `telemetry.recommendations()`
can tell you which nodes should be flipped to `tier: claude` outright.
"""
import time
from typing import Optional

from rich.console import Console

from .claude_node import ClaudeNode, NodeResult
from .logger import log_run

_console = Console(legacy_windows=False)

TIER_FREE = "free"
TIER_VERIFY = "free-then-verify"
TIER_CLAUDE = "claude"
VALID_TIERS = {TIER_FREE, TIER_VERIFY, TIER_CLAUDE}

DEFAULT_TASK_TYPE = "compress"


class GateFailure(Exception):
    """Free-chain output did not pass the quality gate."""


def check_gate(text: str, gate: dict) -> None:
    """Raise GateFailure if `text` fails any configured check.

    Every check is a cheap deterministic assertion — no model call. An empty
    `gate` block means "accept anything the free chain returns", which is only
    appropriate for tier: free.
    """
    if not text or not text.strip():
        raise GateFailure("empty output")

    min_chars = gate.get("min_chars")
    if min_chars and len(text.strip()) < int(min_chars):
        raise GateFailure(f"output shorter than min_chars ({len(text.strip())} < {min_chars})")

    max_chars = gate.get("max_chars")
    if max_chars and len(text.strip()) > int(max_chars):
        raise GateFailure(f"output longer than max_chars ({len(text.strip())} > {max_chars})")

    if gate.get("require_json") or gate.get("require_keys"):
        import json

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
            cleaned = cleaned.removeprefix("json").strip()
        try:
            parsed = json.loads(cleaned)
        except (ValueError, IndexError) as exc:
            raise GateFailure(f"output is not valid JSON: {exc}") from exc
        for key in gate.get("require_keys", []):
            if not isinstance(parsed, dict) or key not in parsed:
                raise GateFailure(f"output missing required key {key!r}")

    for pattern in gate.get("forbid_patterns", []):
        if pattern.lower() in text.lower():
            raise GateFailure(f"output contains forbidden pattern {pattern!r}")


class RoutedNode:
    """A pipeline step that prefers the free chain and escalates on demand."""

    def __init__(
        self,
        *,
        tier: str,
        claude_node: ClaudeNode,
        task_type: str = DEFAULT_TASK_TYPE,
        gate: Optional[dict] = None,
    ):
        if tier not in VALID_TIERS:
            raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(VALID_TIERS)}")
        self.tier = tier
        self.claude_node = claude_node
        self.task_type = task_type
        self.gate = gate or {}
        # Displayed by the pipeline runner before the node actually resolves.
        self.model = f"{tier}:{task_type}"
        self.prompt_template = claude_node.prompt_template
        self.system = claude_node.system

    @property
    def memory_context(self):
        return self.claude_node.memory_context

    @memory_context.setter
    def memory_context(self, value):
        self.claude_node.memory_context = value

    def _run_free(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        from llmroute import policy, telemetry
        from llmroute.cache import flatten, system_blocks

        route = policy.resolve(self.task_type)
        if not route.chain:
            raise GateFailure(f"task_type {self.task_type!r} has no free backend configured")

        system = flatten(system_blocks(self.claude_node.system, self.claude_node.memory_context, force=True))
        prompt = self.claude_node._build_prompt(input_text)

        t0 = time.perf_counter()
        result = policy.call(prompt, self.task_type, system=system)
        latency = int((time.perf_counter() - t0) * 1000)

        check_gate(result.text, self.gate)

        model_label = f"{result.backend}:{route.models.get(result.backend, result.backend)} [free]"
        log_run(
            pipeline=pipeline,
            node=node,
            model=model_label,
            input_text=input_text,
            output_text=result.text,
            latency_ms=latency,
            token_count=0,  # free backends do not report comparable token counts
        )
        telemetry.record(
            backend=result.backend,
            outcome="ok",
            task_type=self.task_type,
            latency_ms=latency,
            chars=len(result.text),
            project=pipeline,
            node=node,
            escalated=False,
        )
        return NodeResult(
            output=result.text,
            model=model_label,
            token_count=0,
            latency_ms=latency,
            pipeline=pipeline,
            node=node,
        )

    def run(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        if self.tier == TIER_CLAUDE:
            return self.claude_node.run(pipeline=pipeline, node=node, input_text=input_text)

        try:
            return self._run_free(pipeline=pipeline, node=node, input_text=input_text)
        except Exception as exc:
            if self.tier == TIER_FREE:
                # No escalation configured — the failure is the result.
                raise
            _console.print(f"  [yellow]free chain failed gate for '{node}' ({exc}) — escalating to Claude[/yellow]")

        try:
            from llmroute import telemetry

            telemetry.record(
                backend="claude",
                outcome="ok",
                task_type=self.task_type,
                project=pipeline,
                node=node,
                escalated=True,
            )
        except Exception:
            pass
        return self.claude_node.run(pipeline=pipeline, node=node, input_text=input_text)
