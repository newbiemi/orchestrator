#!/usr/bin/env python3
"""
auto_summarize_all.py — Autonomously summarize all project folders.

Flow:
  1. Scan ~/projects for project subdirectories
  2. Skip folders with fresh summaries (< STALE_DAYS old)
  3. Run orchestrator context_summarizer on each stale/missing project
     - Ollama chunks the source files (free, local)
     - Claude produces the structured summary (1 API call per project)
  4. Log results to ~/projects/context/auto_summarize.log

Run manually:   python auto_summarize_all.py
Auto-triggered: Claude Code SessionStart hook (runs in background)
"""

import os
import sys
import time
import logging
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

PROJECTS_ROOT     = Path.home() / "projects"
ORCHESTRATOR_ROOT = PROJECTS_ROOT / "orchestrator"
STALE_DAYS        = 3      # re-summarize if summary is older than this
LOG_PATH          = PROJECTS_ROOT / "context" / "auto_summarize.log"

# Directories that are not standalone projects
SKIP_FOLDERS = {
    "context", "orchestrator", ".git", "__pycache__",
    "node_modules", ".venv", "venv", "env",
}

# A directory is treated as a project if it contains at least one of these
PROJECT_MARKERS = {
    "package.json", "pyproject.toml", "setup.py",
    "requirements.txt", "CLAUDE.md", "README.md", ".git",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    import io
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace") if hasattr(sys.stdout, "buffer") else sys.stdout
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(stream),
        ],
    )


def is_project(path: Path) -> bool:
    # Level 0: direct markers (e.g. AI_trading/CLAUDE.md)
    if any((path / m).exists() for m in PROJECT_MARKERS):
        return True
    # Level 1: one level deep (e.g. tengo_cafe/retail_operations_system/package.json)
    for sub in path.iterdir():
        if not sub.is_dir():
            continue
        if any((sub / m).exists() for m in PROJECT_MARKERS):
            return True
        # Level 2: two levels deep (e.g. task_organizer/task-parser-project/task-parser/CLAUDE.md)
        for subsub in sub.iterdir():
            if subsub.is_dir() and any((subsub / m).exists() for m in PROJECT_MARKERS):
                return True
    return False


def is_stale(path: Path) -> bool:
    """True if context/summary.md is missing or older than STALE_DAYS."""
    summary = path / "context" / "summary.md"
    if not summary.exists():
        return True
    age_days = (time.time() - summary.stat().st_mtime) / 86400
    return age_days > STALE_DAYS


def find_projects() -> list[Path]:
    projects = []
    for item in sorted(PROJECTS_ROOT.iterdir()):
        if not item.is_dir():
            continue
        if item.name in SKIP_FOLDERS or item.name.startswith("."):
            continue
        if is_project(item):
            projects.append(item)
    return projects


OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EXE = r"C:\Users\fahms\AppData\Local\Programs\Ollama\ollama.exe"


def ollama_is_up() -> bool:
    try:
        import httpx
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def ensure_ollama() -> bool:
    """
    Start Ollama if it's not running. Waits up to 30s for it to become ready.
    Returns True if Ollama is reachable after the check/start.
    """
    if ollama_is_up():
        return True

    logging.info("Ollama not running — starting it...")
    try:
        import subprocess
        subprocess.Popen(
            [OLLAMA_EXE, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as exc:
        logging.error(f"Failed to start Ollama: {exc}")
        return False

    # Wait up to 30s for Ollama to become ready
    for attempt in range(1, 11):
        time.sleep(3)
        if ollama_is_up():
            logging.info(f"Ollama ready after {attempt * 3}s")
            return True
        logging.info(f"  waiting for Ollama... ({attempt * 3}s)")

    logging.error("Ollama did not become ready within 30s")
    return False


def run_pipeline(project_path: Path) -> bool:
    """
    Run context_summarizer on one project.
    Imports orchestrator without changing cwd so module paths stay stable.
    Returns True on success.
    """
    if str(ORCHESTRATOR_ROOT) not in sys.path:
        sys.path.insert(0, str(ORCHESTRATOR_ROOT))

    # Load .env from orchestrator so API keys are available
    try:
        from dotenv import load_dotenv
        load_dotenv(ORCHESTRATOR_ROOT / ".env")
    except ImportError:
        pass

    try:
        from pipelines.context_summarizer import ContextSummarizerPipeline
        result = ContextSummarizerPipeline().run(
            initial_input=str(project_path),
            path=str(project_path),
            no_eval=True,   # skip optimizer for speed during background runs
        )
        return result is not None and result.status == "ok"
    except Exception:
        import traceback
        logging.error(f"  [{project_path.name}] pipeline error:\n{traceback.format_exc()}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging()
    logging.info("--- auto_summarize_all ---")

    if not ensure_ollama():
        logging.warning("Ollama could not be started — skipping summarization.")
        return

    projects = find_projects()
    logging.info(f"Projects found: {[p.name for p in projects]}")

    stale = [p for p in projects if is_stale(p)]
    if not stale:
        logging.info("All summaries are fresh — nothing to do.")
        return

    logging.info(f"Stale ({len(stale)}): {[p.name for p in stale]}")

    ok_list, fail_list = [], []

    for project in stale:
        logging.info(f"[{project.name}] starting...")
        t0 = time.time()
        success = run_pipeline(project)
        elapsed = int(time.time() - t0)

        if success:
            logging.info(f"[{project.name}] done in {elapsed}s")
            ok_list.append(project.name)
        else:
            logging.warning(f"[{project.name}] FAILED after {elapsed}s")
            fail_list.append(project.name)

    logging.info(f"--- done  ok={ok_list}  failed={fail_list} ---")


if __name__ == "__main__":
    main()
