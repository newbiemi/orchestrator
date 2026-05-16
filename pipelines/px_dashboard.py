"""
PX Dashboard Sync Pipeline

Flow:
  1. Run sync_2026_data() from px_dashboard/ingest.py (subprocess)
  2. ClaudeNode — parse sync output and produce a structured sync report

CLI:
  python runner.py --pipeline px_dashboard
  python runner.py --pipeline px_dashboard --no-eval   # skip eval node

API:
  POST /run/px_dashboard  { "input": "" }
"""
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from core.pipeline import Pipeline, EvalMarker
from core.claude_node import ClaudeNode

load_dotenv()
console = Console(legacy_windows=False)

INGEST_SCRIPT = Path(__file__).resolve().parents[2] / "influx_coding" / "dashboard" / "px_dashboard" / "ingest.py"
INGEST_DIR    = INGEST_SCRIPT.parent


def run_sync() -> str:
    """Run ingest.py and capture stdout/stderr. Returns combined output string."""
    result = subprocess.run(
        [sys.executable, str(INGEST_SCRIPT)],
        cwd=str(INGEST_DIR),
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + ("\n[STDERR]\n" + result.stderr if result.stderr.strip() else "")
    if result.returncode != 0:
        output = f"[EXIT CODE {result.returncode}]\n" + output
    return output.strip()


class PxDashboardPipeline(Pipeline):
    name = "px_dashboard"

    def build_nodes(self, **kwargs):
        return [
            ("sync_report", ClaudeNode(
                model="claude-haiku-4-5-20251001",
                system=(
                    "You are a data pipeline monitor for PXInsights — an internal "
                    "People Experience analytics dashboard at Influx. "
                    "You receive the raw stdout from a MotherDuck sync script and "
                    "produce a concise, structured sync report. "
                    "Include: tables updated with row counts, any WARNING lines "
                    "flagging dropped rows, and a one-line status (OK / WARNINGS). "
                    "Use markdown. Keep it under 20 lines."
                ),
                prompt_template=(
                    "Here is the output from the PX Dashboard sync:\n\n"
                    "{input}\n\n"
                    "Produce a sync report."
                ),
                max_tokens=512,
                temperature=0.1,
            )),
        ]

    def run(self, initial_input: str = "", **kwargs):
        no_eval = kwargs.get("no_eval", False)

        console.print("[bold cyan]PX Dashboard — running sync...[/bold cyan]")
        sync_output = run_sync()
        console.print(sync_output)

        console.print("\n[bold cyan]Generating sync report...[/bold cyan]")
        nodes = self.build_nodes(**kwargs)
        text = sync_output
        for node_name, node in nodes:
            text = node.run(text)
            console.print(f"\n[dim]── {node_name} ──[/dim]")
            console.print(text)

        return text
