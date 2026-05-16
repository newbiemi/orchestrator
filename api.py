"""
FastAPI server for orchestrator.
Activate with: python runner.py --serve [--port 8000]

Endpoints:
  POST /run/{pipeline_name}   — run a pipeline, returns structured JSON
  GET  /pipelines             — list all configured pipelines
  GET  /logs/{pipeline_name}  — recent runs for a pipeline
  GET  /health                — liveness check
"""
import io
import sys
import time

# Windows UTF-8 fix
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Any, Optional

from core.config import list_pipelines, load_pipeline_config
from core.logger import fetch_runs

app = FastAPI(
    title="Orchestrator API",
    description="LLM pipeline runner — Claude + Ollama",
    version="1.0.0",
)


# ── Request / Response models ──────────────────────────────────────────────────

class RunRequest(BaseModel):
    input: str = Field(..., description="Initial input passed to the first node")
    # Pipeline-specific optional params
    ticker: Optional[str] = Field(None, description="Ticker symbol (trading pipeline)")
    path:   Optional[str] = Field(None, description="Directory path (context_summarizer)")

class StepLog(BaseModel):
    node: str
    model: str
    token_count: int
    latency_ms: int
    output_preview: str

class RunResponse(BaseModel):
    pipeline: str
    status: str
    output: str
    steps: list[StepLog]
    total_tokens: int
    total_latency_ms: int
    error: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pipeline_result_to_response(name: str, result) -> RunResponse:
    steps = [
        StepLog(
            node=s.node,
            model=s.model,
            token_count=s.token_count,
            latency_ms=s.latency_ms,
            output_preview=s.output[:200],
        )
        for s in result.steps
    ]
    return RunResponse(
        pipeline=name,
        status=result.status,
        output=result.final_output,
        steps=steps,
        total_tokens=result.total_tokens,
        total_latency_ms=result.total_latency_ms,
        error=result.error,
    )


def _run_pipeline(name: str, req: RunRequest):
    """Dispatch to the right pipeline class."""
    if name == "trading":
        from pipelines.trading import TradingPipeline
        ticker = req.ticker or req.input or "AAPL"
        return TradingPipeline().run(initial_input=ticker, ticker=ticker)

    if name == "context_summarizer":
        from pipelines.context_summarizer import ContextSummarizerPipeline
        path = req.path or req.input or "."
        return ContextSummarizerPipeline().run(initial_input=path, path=path)

    # Generic: any pipeline defined in pipelines.yaml
    from core.pipeline import ConfiguredPipeline
    return ConfiguredPipeline.from_yaml(name).run(initial_input=req.input)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/pipelines")
def get_pipelines():
    names = list_pipelines()
    result = []
    for name in names:
        cfg = load_pipeline_config(name)
        result.append({
            "name": name,
            "description": cfg.get("description", ""),
            "triggers": cfg.get("triggers", []),
            "node_count": len(cfg.get("nodes", [])),
        })
    return {"pipelines": result}


@app.post("/run/{pipeline_name}", response_model=RunResponse)
def run_pipeline(pipeline_name: str, req: RunRequest):
    # Validate pipeline exists
    try:
        load_pipeline_config(pipeline_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        result = _run_pipeline(pipeline_name, req)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")

    if result is None:
        raise HTTPException(status_code=500, detail="Pipeline returned no result")

    return _pipeline_result_to_response(pipeline_name, result)


@app.get("/logs/{pipeline_name}")
def get_logs(pipeline_name: str, limit: int = 20):
    runs = fetch_runs(pipeline=pipeline_name, limit=limit)
    return {"pipeline": pipeline_name, "runs": runs}


@app.get("/logs")
def get_all_logs(limit: int = 20):
    runs = fetch_runs(limit=limit)
    return {"runs": runs}
