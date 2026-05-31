"""
PythonNode — run a registered Python callable as a pipeline node.

Callable contract:
    fn(input_text: str) -> dict | str

If the callable returns a dict, the result is JSON-serialized so the next
(Claude) node receives clean structured text. A str result passes through
unchanged.

Config (pipelines.yaml):
  - name: summarize_prices
    type: python
    fn: pipelines.transforms.summarize_prices   # dotted import path

The dotted fn path is resolved at call time (lazy), so the callable's
module is only imported when the node runs — same pattern as SubprocessNode
deferring shlex.split to run().
"""
import importlib
import json
import time
from typing import Optional

from .claude_node import NodeResult
from .logger import log_run


def _resolve(dotted: str):
    """Import and return the callable at `module.path:attribute`."""
    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        raise ImportError(f"fn must be a dotted path like 'pkg.module.fn', got {dotted!r}")
    module = importlib.import_module(module_path)
    return getattr(module, attr)


class PythonNode:
    """
    Calls a deterministic Python function and returns its output as a NodeResult.
    Drop-in replacement for OllamaNode where the task is a deterministic transform
    of structured input (numeric data → template string, signals → interpretation, etc.).

    The NodeResult contract is identical to OllamaNode / SubprocessNode so
    _PipelineRunner.execute() needs no changes.
    """

    def __init__(
        self,
        *,
        fn: str,
        # Accept (and silently ignore) standard node-level kwargs so
        # _node_from_config can pass them uniformly without per-type filtering.
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        prompt_template: Optional[str] = None,
    ):
        self._fn_dotted = fn
        self.model = "python"   # shown in pipeline summary table + DB logs

    def run(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        t0 = time.perf_counter()
        try:
            fn = _resolve(self._fn_dotted)
            raw = fn(input_text)
            output = json.dumps(raw) if isinstance(raw, dict) else str(raw)
            status, error = "ok", None
        except Exception as exc:
            output = f"[ERROR] {exc}"
            status, error = "error", str(exc)

        latency = int((time.perf_counter() - t0) * 1000)
        log_run(
            pipeline=pipeline,
            node=node,
            model=self.model,
            input_text=self._fn_dotted,
            output_text=output[:500],
            latency_ms=latency,
            token_count=0,
            status=status,
            error=error,
        )
        return NodeResult(
            output=output,
            model=self.model,
            token_count=0,
            latency_ms=latency,
            pipeline=pipeline,
            node=node,
        )
