"""
SubprocessNode — run an external shell command as a pipeline node.

The command is run with capture_output=True; stdout + stderr are combined
and returned as NodeResult.output so the next node can consume them.

Config (pipelines.yaml):
  - name: run_scan
    type: subprocess
    command: "python /path/to/script.py --flag"
    timeout: 600              # seconds, optional (default 600)
    keep_keywords:            # if set, only lines containing any keyword are kept
      - "BUY"
      - "SELL"
      - "HOLD"

Environment variables in `command` are expanded (e.g. $BOT_SCRIPT).
"""
import os
import re
import shlex
import subprocess
import sys
import time
from typing import Optional

from .claude_node import NodeResult
from .logger import log_run

# Strip Python logging timestamps: "2026-05-18 13:15:29,231 [INFO] "
_LOG_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \[\w+\]\s*")


class SubprocessNode:
    """Runs an external command and captures its output as a pipeline step."""

    def __init__(
        self,
        *,
        command: str,
        timeout: int = 600,
        keep_keywords: Optional[list] = None,
        # Accept (and ignore) node-level kwargs that don't apply to subprocess
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        prompt_template: Optional[str] = None,
    ):
        self.command = command
        self.timeout = timeout
        self.keep_keywords = keep_keywords  # filter: only keep lines with these words
        self.model = "subprocess"

    def _clean(self, raw: str) -> str:
        """Strip log timestamps and optionally filter to keyword-matching lines."""
        lines = []
        for line in raw.splitlines():
            line = _LOG_PREFIX.sub("", line).strip()
            if not line:
                continue
            if self.keep_keywords:
                if not any(kw.upper() in line.upper() for kw in self.keep_keywords):
                    continue
            lines.append(line)
        return "\n".join(lines)

    def run(self, *, pipeline: str, node: str, input_text: str) -> NodeResult:
        cmd_str = os.path.expandvars(self.command)
        cmd = shlex.split(cmd_str, posix=(sys.platform != "win32"))

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"          # force UTF-8 stdout on Windows
        env["PYTHONIOENCODING"] = "utf-8"

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            raw = proc.stdout
            if proc.stderr.strip():
                raw = raw + "\n" + proc.stderr
            output = self._clean(raw)
            if proc.returncode != 0:
                output = f"[EXIT {proc.returncode}]\n" + output
            if not output.strip():
                output = raw.strip()[-2000:]  # fallback: unfiltered tail
            latency = int((time.perf_counter() - t0) * 1000)
            log_run(
                pipeline=pipeline,
                node=node,
                model=self.model,
                input_text=cmd_str,
                output_text=output[:500],
                latency_ms=latency,
                token_count=0,
            )
            return NodeResult(
                output=output,
                model=self.model,
                token_count=0,
                latency_ms=latency,
                pipeline=pipeline,
                node=node,
            )
        except subprocess.TimeoutExpired:
            latency = int((time.perf_counter() - t0) * 1000)
            msg = f"[TIMEOUT after {self.timeout}s] {cmd_str}"
            log_run(pipeline=pipeline, node=node, model=self.model,
                    input_text=cmd_str, output_text=msg,
                    latency_ms=latency, token_count=0, status="error", error=msg)
            return NodeResult(output=msg, model=self.model, token_count=0,
                              latency_ms=latency, pipeline=pipeline, node=node)
        except Exception as exc:
            latency = int((time.perf_counter() - t0) * 1000)
            msg = f"[ERROR] {exc}"
            log_run(pipeline=pipeline, node=node, model=self.model,
                    input_text=cmd_str, output_text=msg,
                    latency_ms=latency, token_count=0, status="error", error=str(exc))
            return NodeResult(output=msg, model=self.model, token_count=0,
                              latency_ms=latency, pipeline=pipeline, node=node)
