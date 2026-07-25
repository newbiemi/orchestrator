"""
Base Pipeline — chains nodes, passes output→input, logs the full run.

Two usage patterns:
  1. Subclass Pipeline and implement build_nodes() — full Python control.
  2. Use ConfiguredPipeline.from_yaml(name) — zero Python, driven entirely
     by pipelines.yaml. Good for simple linear chains.

Memory integration (Phase 7):
  Before running nodes, the pipeline recalls the last N relevant memories
  and injects them into the first node's system prompt as context.
  After completion, the run outcome is stored as a new memory (with score
  from the prompt optimizer when available).
"""
import time
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.table import Table

from .claude_node import ClaudeNode, NodeResult
from .ollama_node import OllamaNode
from .memory import recall_memories, format_memories_for_context, store_memory
from .prompt_optimizer import optimize_run

console = Console(legacy_windows=False)


@dataclass
class PipelineResult:
    pipeline: str
    final_output: str
    steps: list[NodeResult] = field(default_factory=list)
    total_latency_ms: int = 0
    total_tokens: int = 0
    status: str = "ok"
    error: Optional[str] = None

    def print_summary(self) -> None:
        table = Table(title=f"Pipeline: {self.pipeline}", show_lines=True)
        table.add_column("Node", style="cyan")
        table.add_column("Model", style="magenta")
        table.add_column("Tokens", justify="right")
        table.add_column("Latency (ms)", justify="right")
        table.add_column("Output preview")

        for step in self.steps:
            preview = step.output[:80].replace("\n", " ")
            table.add_row(
                step.node,
                step.model,
                str(step.token_count),
                str(step.latency_ms),
                preview,
            )

        console.print(table)
        console.print(
            f"[bold]Total:[/bold] {self.total_tokens} tokens | "
            f"{self.total_latency_ms} ms | status={self.status}"
        )


# ── Eval marker (Phase 9) ──────────────────────────────────────────────────────

class EvalMarker:
    """
    Sentinel placed into the node list where an 'eval' node is defined in YAML.
    The pipeline's run() loop detects this and triggers the prompt optimizer
    on the previous node's output instead of running an LLM call.
    """

    def __init__(self, threshold: int = 7):
        self.threshold = threshold
        self.model = "eval"  # for display in the pipeline loop


# ── Per-run state helpers ──────────────────────────────────────────────────────

@dataclass
class _LastStep:
    name: str
    node: ClaudeNode | OllamaNode


class _PipelineRunner:
    """Single-use executor for one pipeline run. Owns all per-run mutable state."""

    def __init__(
        self,
        pipeline: "Pipeline",
        initial_input: str,
        *,
        no_eval: bool,
        nodes: list,
    ):
        self.pipeline = pipeline
        self.initial_input = initial_input
        self.no_eval = no_eval
        self.nodes = nodes
        self.result = PipelineResult(pipeline=pipeline.name, final_output="")
        self.current_input = initial_input
        self.last_step: _LastStep | None = None
        self.final_score: float | None = None
        self._t_start: float = 0.0

    def execute(self) -> PipelineResult:
        console.rule(f"[bold blue]Pipeline: {self.pipeline.name}")
        self._t_start = time.perf_counter()
        self.pipeline._inject_memory_context(self.nodes)

        for node_name, node in self.nodes:
            if isinstance(node, EvalMarker):
                self._dispatch_eval(node_name, node)
                continue
            if not self._execute_node(node_name, node):
                break

        return self._finalize()

    def _execute_node(self, node_name: str, node: ClaudeNode | OllamaNode) -> bool:
        """Run one LLM node. Returns False on failure (caller should break)."""
        console.print(f"  [yellow]> {node_name}[/yellow] ({node.model})")
        try:
            step = node.run(
                pipeline=self.pipeline.name,
                node=node_name,
                input_text=self.current_input,
            )
            self.result.steps.append(step)
            self.current_input = step.output
            self.last_step = _LastStep(name=node_name, node=node)
            return True
        except Exception as exc:
            self.result.status = "error"
            self.result.error = f"{node_name}: {exc}"
            console.print(f"  [red]x {node_name} failed: {exc}[/red]")
            return False

    def _dispatch_eval(self, node_name: str, marker: EvalMarker) -> None:
        """Trigger the prompt optimizer on the previous node's output."""
        if self.no_eval:
            console.print(f"  [dim]> {node_name} skipped (--no-eval)[/dim]")
            return
        if self.last_step is None or not self.result.steps:
            return

        console.print(f"  [yellow]> {node_name}[/yellow] (eval / ollama)")
        input_for_eval = (
            self.result.steps[-1].output if len(self.result.steps) > 1
            else self.initial_input
        )
        optimized_output, score = optimize_run(
            pipeline=self.pipeline.name,
            node_name=self.last_step.name,
            node_instance=self.last_step.node,
            input_text=input_for_eval,
            output_text=self.current_input,
            prompt_template=getattr(self.last_step.node, "prompt_template", None),
        )
        self.current_input = optimized_output
        self.final_score = score

    def _finalize(self) -> PipelineResult:
        """Set totals, persist memory with score, print summary."""
        self.result.final_output = self.current_input
        self.result.total_latency_ms = int((time.perf_counter() - self._t_start) * 1000)
        self.result.total_tokens = sum(s.token_count for s in self.result.steps)

        if self.result.status == "ok":
            console.print("[green]Pipeline complete[/green]")
            store_memory(
                pipeline=self.pipeline.name,
                input_text=self.initial_input,
                output=self.result.final_output,
                score=self.final_score,
            )
            console.print("  [dim]Run stored in memory[/dim]")

        self.result.print_summary()
        return self.result


# ── Base pipeline ──────────────────────────────────────────────────────────────

class Pipeline:
    """
    Base class for all orchestrator pipelines.

    Subclasses define `build_nodes()` which returns an ordered list of
    (node_name, node_instance) tuples. The pipeline feeds each node's
    output as the next node's input_text.

    Example:
        class TradingPipeline(Pipeline):
            name = "trading"

            def build_nodes(self, **kwargs):
                ticker = kwargs.get("ticker", "AAPL")
                return [
                    ("fetch_summary", OllamaNode(...)),
                    ("decision",      ClaudeNode(...)),
                ]
    """

    name: str = "base"

    # Fallback when memory_context_n is absent from pipelines.yaml; YAML wins.
    memory_context_n: int = 5

    def build_nodes(self, **kwargs) -> list[tuple[str, ClaudeNode | OllamaNode]]:
        raise NotImplementedError("Subclasses must implement build_nodes()")

    def _inject_memory_context(self, nodes: list) -> None:
        """
        Recall recent memories and prepend them to the first node's system prompt.
        Modifies the first node in-place.
        """
        from .config import get_memory_context_n
        try:
            n = get_memory_context_n(self.name)
        except (ValueError, FileNotFoundError):
            n = self.memory_context_n

        if n <= 0 or not nodes:
            return

        memories = recall_memories(pipeline=self.name, n=n)
        if not memories:
            return

        context_block = format_memories_for_context(memories)
        if not context_block:
            return

        _, first_node = nodes[0]
        if isinstance(first_node, ClaudeNode):
            # Keep base system stable for cache prefix; memory goes in its own block
            first_node.memory_context = context_block
        else:
            first_node.system = f"{first_node.system}\n\n{context_block}"
        console.print(f"  [dim]Injected {len(memories)} memories into '{nodes[0][0]}'[/dim]")

    def run(self, initial_input: str, **kwargs) -> PipelineResult:
        no_eval = kwargs.pop("no_eval", False)
        nodes = self.build_nodes(**kwargs)
        return _PipelineRunner(self, initial_input, no_eval=no_eval, nodes=nodes).execute()


# ── Config-driven pipeline ────────────────────────────────────────────────────

def _node_from_config(cfg: dict) -> tuple[str, "ClaudeNode | OllamaNode | EvalMarker"]:
    """Instantiate a node from a YAML node block."""
    node_type = cfg.get("type", "ollama").lower()
    name = cfg["name"]

    kwargs = {
        "model":           cfg.get("model"),
        "system":          cfg.get("system", "You are a helpful assistant."),
        "temperature":     float(cfg.get("temperature", 0.2)),
        "prompt_template": cfg.get("prompt_template"),
    }
    # Remove None values so node __init__ defaults apply
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    if node_type == "claude":
        if "max_tokens" in cfg:
            kwargs["max_tokens"] = int(cfg["max_tokens"])
        claude = ClaudeNode(**kwargs)
        # `tier` is opt-in: absent (or "claude") keeps the original behaviour
        # byte-for-byte, so existing pipelines are unaffected by this feature.
        tier = str(cfg.get("tier", "claude")).lower()
        if tier == "claude":
            return name, claude
        from .routed_node import DEFAULT_TASK_TYPE, RoutedNode
        return name, RoutedNode(
            tier=tier,
            claude_node=claude,
            task_type=cfg.get("task_type", DEFAULT_TASK_TYPE),
            gate=cfg.get("gate"),
        )

    if node_type == "ollama":
        return name, OllamaNode(**kwargs)

    if node_type == "eval":
        threshold = int(cfg.get("threshold", 7))
        return name, EvalMarker(threshold=threshold)

    if node_type == "subprocess":
        from .subprocess_node import SubprocessNode
        return name, SubprocessNode(
            command=cfg.get("command", ""),
            timeout=int(cfg.get("timeout", 600)),
            keep_keywords=cfg.get("keep_keywords") or None,
        )

    if node_type == "python":
        from .python_node import PythonNode
        return name, PythonNode(fn=cfg["fn"], **kwargs)

    raise ValueError(f"Unknown node_type {node_type!r} in node {name!r}")


class ConfiguredPipeline(Pipeline):
    """
    A pipeline driven entirely by pipelines.yaml — no Python subclass needed.

    Usage:
        result = ConfiguredPipeline.from_yaml("trading").run("AAPL")
    """

    def __init__(self, name: str):
        self.name = name
        self._node_configs: list[dict] = []

    @classmethod
    def from_yaml(cls, name: str) -> "ConfiguredPipeline":
        from .config import load_pipeline_config, get_node_configs
        load_pipeline_config(name)  # validates the name exists
        instance = cls(name)
        instance._node_configs = get_node_configs(name)
        return instance

    def build_nodes(self, **kwargs) -> list[tuple[str, "ClaudeNode | OllamaNode | EvalMarker"]]:
        return [_node_from_config(cfg) for cfg in self._node_configs]
