"""
Unified entry point for orchestrator.

Usage:
  python runner.py --list                              # list all configured pipelines
  python runner.py --pipeline trading --ticker AAPL   # run trading pipeline
  python runner.py --pipeline context_summarizer --path ./myproject
  python runner.py --logs [--pipeline trading] [-n 20]
"""
import argparse
import io
import sys

# Force UTF-8 stdout/stderr on Windows (prevents Rich unicode rendering errors)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrator pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pipeline", help="Pipeline name to run")
    parser.add_argument("--ticker",   default="AAPL",  help="Ticker symbol (trading pipeline)")
    parser.add_argument("--path",     default=".",     help="Directory path (context_summarizer)")
    parser.add_argument("--input",                     help="Arbitrary initial input (generic pipelines)")
    parser.add_argument("--logs",     action="store_true", help="Show recent run logs")
    parser.add_argument("--list",     action="store_true", help="List all available pipelines")
    parser.add_argument("--serve",    action="store_true", help="Start FastAPI server")
    parser.add_argument("--port",     type=int, default=8000, help="API server port (default 8000)")
    parser.add_argument("-n", "--limit", type=int, default=20, help="Log rows to show")
    parser.add_argument("--no-eval",  action="store_true", help="Skip prompt optimizer / eval nodes")
    args = parser.parse_args()

    if args.serve:
        _start_server(args.port)
        return

    if args.list:
        _list_pipelines()
        return

    if args.logs:
        _show_logs(args.pipeline, args.limit)
        return

    if not args.pipeline:
        parser.print_help()
        sys.exit(1)

    _run_pipeline(args)


def _run_pipeline(args):
    name = args.pipeline.lower()

    # ── Named pipelines with their own Python classes ────────────────────────
    no_eval = getattr(args, "no_eval", False)

    if name == "trading":
        from pipelines.trading import TradingPipeline
        TradingPipeline().run(initial_input=args.ticker, ticker=args.ticker, no_eval=no_eval)

    elif name == "context_summarizer":
        from pipelines.context_summarizer import ContextSummarizerPipeline
        ContextSummarizerPipeline().run(initial_input=args.path, path=args.path, no_eval=no_eval)

    elif name == "px_dashboard":
        from pipelines.px_dashboard import PxDashboardPipeline
        PxDashboardPipeline().run(initial_input=args.input or "", no_eval=no_eval)

    # ── Fallback: run any pipeline defined in pipelines.yaml ─────────────────
    else:
        try:
            from core.pipeline import ConfiguredPipeline
            initial = args.input or args.ticker or args.path or ""
            ConfiguredPipeline.from_yaml(name).run(initial_input=initial, no_eval=no_eval)
        except ValueError as exc:
            print(f"Error: {exc}")
            sys.exit(1)


def _start_server(port: int = 8000):
    import uvicorn
    from rich.console import Console
    c = Console(legacy_windows=False)
    c.print(f"[bold green]Orchestrator API starting on http://localhost:{port}[/bold green]")
    c.print(f"[dim]Docs at http://localhost:{port}/docs[/dim]")
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)


def _list_pipelines():
    from core.config import list_pipelines, load_pipeline_config
    from rich.console import Console
    from rich.table import Table

    pipelines = list_pipelines()
    if not pipelines:
        print("No pipelines defined in pipelines.yaml.")
        return

    console = Console(legacy_windows=False)
    table = Table(title="Available Pipelines", show_lines=False)
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Triggers")
    table.add_column("Nodes", justify="right")

    for name in pipelines:
        cfg = load_pipeline_config(name)
        table.add_row(
            name,
            cfg.get("description", "—"),
            ", ".join(cfg.get("triggers", [])),
            str(len(cfg.get("nodes", []))),
        )
    console.print(table)


def _show_logs(pipeline, limit):
    from core.logger import fetch_runs
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich import box

    runs = fetch_runs(pipeline=pipeline, limit=limit)
    if not runs:
        print("No runs found.")
        return

    console = Console(legacy_windows=False)

    # ── Runs table ────────────────────────────────────────────────────────────
    title = f"Last {len(runs)} runs" + (f" — {pipeline}" if pipeline else " — all pipelines")
    table = Table(title=title, show_lines=False, box=box.SIMPLE_HEAVY)
    table.add_column("ID",       style="dim",    justify="right", width=5)
    table.add_column("Pipeline", style="cyan",   width=20)
    table.add_column("Node",     style="yellow", width=18)
    table.add_column("Model",    style="magenta",width=22)
    table.add_column("Tokens",   justify="right",width=8)
    table.add_column("Latency",  justify="right",width=10)
    table.add_column("Status",   width=7)
    table.add_column("Timestamp",width=19)

    for r in runs:
        status_style = "green" if r["status"] == "ok" else "red"
        latency = f"{r['latency_ms']:,} ms"
        table.add_row(
            str(r["id"]),
            r["pipeline"],
            r["node"],
            r["model"],
            f"{r['token_count']:,}",
            latency,
            f"[{status_style}]{r['status']}[/{status_style}]",
            r["timestamp"][:19].replace("T", " "),
        )

    console.print(table)

    # ── Summary stats ─────────────────────────────────────────────────────────
    ok_runs    = [r for r in runs if r["status"] == "ok"]
    err_runs   = [r for r in runs if r["status"] != "ok"]
    total_tok  = sum(r["token_count"] for r in runs)
    avg_lat    = int(sum(r["latency_ms"] for r in ok_runs) / len(ok_runs)) if ok_runs else 0
    max_lat    = max((r["latency_ms"] for r in runs), default=0)
    pipelines  = sorted(set(r["pipeline"] for r in runs))

    stats = (
        f"[bold]Runs:[/bold] {len(runs)}  "
        f"[green]ok={len(ok_runs)}[/green]  "
        f"[red]err={len(err_runs)}[/red]  |  "
        f"[bold]Tokens:[/bold] {total_tok:,}  |  "
        f"[bold]Avg latency:[/bold] {avg_lat:,} ms  "
        f"[bold]Max:[/bold] {max_lat:,} ms  |  "
        f"[bold]Pipelines:[/bold] {', '.join(pipelines)}"
    )
    console.print(Panel(stats, expand=False))


if __name__ == "__main__":
    main()
