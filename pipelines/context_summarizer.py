"""
Context Summarizer Pipeline — Phase 4

Flow:
  1. Walk a directory, collect source files (code + markdown)
  2. Chunk files into batches that fit Ollama's context
  3. OllamaNode — summarize each chunk
  4. ClaudeNode — produce a structured CLAUDE.md-ready summary
  5. Write output to <path>/context/summary.md

CLI:
  python runner.py --pipeline context_summarizer --path ./myproject
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from core.pipeline import Pipeline, EvalMarker
from core.claude_node import ClaudeNode
from core.ollama_node import OllamaNode
from core.config import load_pipeline_config, get_default_claude_model

load_dotenv()
console = Console(legacy_windows=False)

# ── File collection ────────────────────────────────────────────────────────────

INCLUDE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".md", ".yaml", ".yml", ".toml", ".json",
    ".env.example", ".sql", ".sh",
}

EXCLUDE_DIRS = {
    "__pycache__", "node_modules", ".git", ".next", "dist",
    "build", "venv", ".venv", "env", "backtest_reports",
}

EXCLUDE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "summary.md", "session_log.md",
}

MAX_FILE_CHARS = 8_000    # truncate individual large files
CHUNK_CHARS    = 24_000   # max chars per Ollama batch


def collect_files(root: Path) -> list[Path]:
    """
    Walk the directory tree, pruning excluded dirs before descending.
    Uses os.walk(topdown=True) so EXCLUDE_DIRS are never entered — avoids
    Windows long-path errors in node_modules and similar deep trees.
    """
    import os as _os
    files = []
    for dirpath, dirnames, filenames in _os.walk(root, topdown=True):
        # Prune in-place so os.walk never descends into excluded dirs
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not d.startswith(".")
        ]
        for fname in sorted(filenames):
            if fname in EXCLUDE_FILES:
                continue
            fpath = Path(dirpath) / fname
            if fpath.suffix in INCLUDE_EXTENSIONS or fname == ".env.example":
                files.append(fpath)
    return files


def files_to_chunks(files: list[Path], root: Path) -> list[str]:
    """Pack files into text chunks under CHUNK_CHARS each."""
    chunks, current, current_len = [], [], 0

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n... [truncated]"

        rel = f.relative_to(root)
        block = f"### {rel}\n```\n{text}\n```\n"

        if current_len + len(block) > CHUNK_CHARS and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0

        current.append(block)
        current_len += len(block)

    if current:
        chunks.append("\n".join(current))

    return chunks


# ── Pipeline class ─────────────────────────────────────────────────────────────

class ContextSummarizerPipeline(Pipeline):
    name = "context_summarizer"

    def build_nodes(self, **kwargs) -> list[tuple[str, ClaudeNode | OllamaNode]]:
        """
        Only returns the structured_summary + eval nodes.
        Chunk summarization is handled as pre-processing in run() because
        it requires N Ollama calls (one per chunk) rather than a single linear step.
        """
        cfg = load_pipeline_config(self.name)
        nodes_cfg = {n["name"]: n for n in cfg.get("nodes", [])}
        final_cfg = nodes_cfg.get("structured_summary", {})
        eval_cfg  = next((n for n in cfg.get("nodes", []) if n.get("type") == "eval"), {})

        final_node = ClaudeNode(
            model=final_cfg.get("model") or get_default_claude_model(),
            system=final_cfg.get("system", "You produce structured CLAUDE.md-ready project summaries."),
            prompt_template=final_cfg.get("prompt_template"),
            max_tokens=int(final_cfg.get("max_tokens", 2048)),
            temperature=float(final_cfg.get("temperature", 0.1)),
        )

        nodes = [("structured_summary", final_node)]
        if eval_cfg:
            nodes.append(("eval", EvalMarker(threshold=int(eval_cfg.get("threshold", 6)))))
        return nodes

    def run(self, initial_input: str = ".", **kwargs):
        path = Path(kwargs.get("path", initial_input)).resolve()

        if not path.exists():
            console.print(f"[red]Path not found: {path}[/red]")
            return

        console.print(f"  [dim]Scanning {path}...[/dim]")
        files = collect_files(path)
        if not files:
            console.print("[yellow]No supported files found.[/yellow]")
            return

        console.print(f"  [dim]Found {len(files)} files — building chunks...[/dim]")
        chunks = files_to_chunks(files, path)
        console.print(f"  [dim]{len(chunks)} chunk(s) to summarize[/dim]")

        # ── Step 1: fan-out chunk summarization (pre-processing, not part of base pipeline) ──
        cfg = load_pipeline_config(self.name)
        nodes_cfg = {n["name"]: n for n in cfg.get("nodes", [])}
        chunk_cfg = nodes_cfg.get("chunk_summarizer", {})

        chunk_node = OllamaNode(
            model=chunk_cfg.get("model", "qwen2.5-coder"),
            system=chunk_cfg.get("system", "You summarize source code and docs concisely."),
            prompt_template=chunk_cfg.get("prompt_template"),
            temperature=float(chunk_cfg.get("temperature", 0.1)),
        )

        chunk_summaries = []
        for i, chunk in enumerate(chunks, 1):
            console.print(f"  [yellow]> chunk_summarizer {i}/{len(chunks)}[/yellow] ({chunk_node.model})")
            step = chunk_node.run(
                pipeline=self.name,
                node=f"chunk_summarizer_{i}",
                input_text=chunk,
            )
            chunk_summaries.append(step.output)

        combined = "\n\n---\n\n".join(chunk_summaries)

        # ── Step 2: base pipeline handles structured_summary + eval + memory ──
        result = super().run(initial_input=combined, **kwargs)

        if result and result.status == "ok":
            out_dir = path / "context"
            out_dir.mkdir(exist_ok=True)
            out_file = out_dir / "summary.md"
            from datetime import datetime, timezone
            header = (
                f"# Project Context Summary\n"
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
                f"Root: `{path}`\n\n"
            )
            out_file.write_text(header + result.final_output, encoding="utf-8")
            console.print(f"\n[green]Summary written to {out_file}[/green]")
            console.print(f"[dim]Chunks: {len(chunks)} | Output: {len(result.final_output)} chars[/dim]")

        return result
