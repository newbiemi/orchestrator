"""
Prompt Optimizer — Phase 8

After each pipeline run:
  1. Score the output (rule-based format check, then Ollama self-eval)
  2. If score >= threshold → save prompt variant as "winner" in pipelines.yaml
  3. If score < threshold → mutate prompt (ask Ollama to improve it) and retry
  4. Max 2 retries per run to control cost and latency

Scoring priority:
  1. Rule-based: check output matches expected format/schema
  2. Self-eval:  Ollama rates output quality 1-10 given the input

All optimizer activity is logged to core/logs/optimizer.db (separate from runs).
Works entirely in Ollama-only mode — never calls Claude.
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console

from .ollama_node import OllamaNode
from .config import get_optimizer_config, save_winning_prompt

console = Console(legacy_windows=False)

OPT_DB_PATH = Path(__file__).parent / "logs" / "optimizer.db"


# ── Optimizer DB ───────────────────────────────────────────────────────────────

def _get_opt_conn() -> sqlite3.Connection:
    OPT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OPT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_opt_db() -> None:
    with _get_opt_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS optimizer_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline        TEXT    NOT NULL,
                node            TEXT    NOT NULL,
                attempt         INTEGER NOT NULL,
                input_preview   TEXT,
                output_preview  TEXT,
                rule_score      REAL,
                eval_score      REAL,
                final_score     REAL,
                action          TEXT,
                prompt_before   TEXT,
                prompt_after    TEXT,
                timestamp       TEXT    NOT NULL
            )
        """)


def _log_optimizer(
    *,
    pipeline: str,
    node: str,
    attempt: int,
    input_text: str,
    output_text: str,
    rule_score: Optional[float],
    eval_score: Optional[float],
    final_score: float,
    action: str,
    prompt_before: Optional[str] = None,
    prompt_after: Optional[str] = None,
) -> None:
    _init_opt_db()
    ts = datetime.now(timezone.utc).isoformat()
    with _get_opt_conn() as conn:
        conn.execute(
            """INSERT INTO optimizer_log
               (pipeline, node, attempt, input_preview, output_preview,
                rule_score, eval_score, final_score, action,
                prompt_before, prompt_after, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pipeline, node, attempt,
             input_text[:500], output_text[:500],
             rule_score, eval_score, final_score, action,
             prompt_before, prompt_after, ts),
        )


# ── Scoring ────────────────────────────────────────────────────────────────────

# Rule-based format validators per pipeline
# Returns (score 0-10, reason) or None if no rule applies
PIPELINE_RULES: dict[str, callable] = {}


def register_rule(pipeline: str):
    """Decorator to register a rule-based scorer for a pipeline."""
    def decorator(fn):
        PIPELINE_RULES[pipeline] = fn
        return fn
    return decorator


@register_rule("trading")
def _score_trading(output: str) -> tuple[float, str]:
    """Trading pipeline: output must contain DECISION: BUY|HOLD|SELL."""
    upper = output.upper()
    has_decision = any(d in upper for d in ("BUY", "HOLD", "SELL"))
    has_format = "DECISION" in upper
    if has_decision and has_format:
        return 8.0, "Correct format: DECISION with BUY/HOLD/SELL"
    if has_decision:
        return 6.0, "Contains decision keyword but wrong format"
    return 3.0, "Missing decision keyword entirely"


def rule_based_score(pipeline: str, output: str) -> Optional[tuple[float, str]]:
    """Apply pipeline-specific rule if one is registered."""
    fn = PIPELINE_RULES.get(pipeline)
    if fn:
        return fn(output)
    return None


def self_eval_score(input_text: str, output_text: str) -> tuple[float, str]:
    """
    Ask Ollama to rate the output quality 1-10.
    Always uses Ollama to minimize cost.
    Returns (score, reason).
    """
    eval_node = OllamaNode(
        model="qwen2.5-coder",
        system=(
            "You are a quality evaluator. Rate the output quality from 1 to 10. "
            "Reply with ONLY valid JSON: {\"score\": <int>, \"reason\": \"<brief reason>\"}"
        ),
        temperature=0.1,
    )

    prompt = (
        f"INPUT:\n{input_text[:1000]}\n\n"
        f"OUTPUT:\n{output_text[:1000]}\n\n"
        "Rate this output for accuracy, usefulness, and format correctness."
    )

    try:
        result = eval_node.run(
            pipeline="_optimizer",
            node="self_eval",
            input_text=prompt,
        )
        # Parse JSON from output, handling possible markdown wrapping
        text = result.output.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        score = float(parsed.get("score", 5))
        reason = parsed.get("reason", "No reason given")
        return min(max(score, 1.0), 10.0), reason
    except Exception as exc:
        console.print(f"  [dim]Self-eval parse failed: {exc} — defaulting to 5.0[/dim]")
        return 5.0, f"Parse error: {exc}"


def compute_score(pipeline: str, input_text: str, output_text: str) -> tuple[float, str]:
    """
    Compute a final score for a pipeline output.
    Priority: rule-based first, then self-eval as tiebreaker.
    Returns (score 1-10, reason).
    """
    rule_result = rule_based_score(pipeline, output_text)
    eval_result = self_eval_score(input_text, output_text)

    if rule_result:
        rule_score, rule_reason = rule_result
        eval_score, eval_reason = eval_result
        # Weighted: 60% rule, 40% self-eval
        final = rule_score * 0.6 + eval_score * 0.4
        reason = f"Rule({rule_score:.1f}): {rule_reason} | Eval({eval_score:.1f}): {eval_reason}"
        return final, reason

    # No rule — self-eval is the full score
    return eval_result


# ── Prompt mutation ────────────────────────────────────────────────────────────

def mutate_prompt(
    current_prompt: str,
    input_text: str,
    output_text: str,
    score: float,
    reason: str,
) -> str:
    """
    Ask Ollama to suggest an improved version of the prompt.
    Returns the mutated prompt text.
    """
    mutator = OllamaNode(
        model="qwen2.5-coder",
        system=(
            "You are a prompt engineer. Given a current prompt template, the input it received, "
            "the output it produced, and a quality score, suggest an improved version of the prompt. "
            "Reply with ONLY the improved prompt template text. Keep {input} as the placeholder. "
            "Make minimal, targeted changes — do not rewrite from scratch."
        ),
        temperature=0.3,
    )

    mutation_input = (
        f"CURRENT PROMPT TEMPLATE:\n{current_prompt}\n\n"
        f"INPUT RECEIVED:\n{input_text[:500]}\n\n"
        f"OUTPUT PRODUCED:\n{output_text[:500]}\n\n"
        f"SCORE: {score:.1f}/10\n"
        f"ISSUE: {reason}\n\n"
        "Suggest an improved prompt template that would produce a higher-quality output."
    )

    try:
        result = mutator.run(
            pipeline="_optimizer",
            node="prompt_mutator",
            input_text=mutation_input,
        )
        return result.output.strip()
    except Exception as exc:
        console.print(f"  [red]Prompt mutation failed: {exc}[/red]")
        return current_prompt  # return unchanged on failure


# ── Main optimizer entry point ─────────────────────────────────────────────────

def optimize_run(
    *,
    pipeline: str,
    node_name: str,
    node_instance,
    input_text: str,
    output_text: str,
    prompt_template: Optional[str] = None,
) -> tuple[str, float]:
    """
    Score a node's output and optionally retry with a mutated prompt.

    Returns (final_output, final_score).

    Flow:
      1. Score the output
      2. If score >= threshold → log as winner, save variant
      3. If score < threshold → mutate prompt, rerun node, re-score
      4. Max retries from config (default 2)
    """
    cfg = get_optimizer_config(pipeline)
    if not cfg["enabled"]:
        return output_text, 0.0

    threshold = cfg["threshold"]
    max_retries = cfg["max_retries"]

    current_output = output_text
    current_prompt = prompt_template or getattr(node_instance, "prompt_template", None) or ""
    best_score = 0.0

    for attempt in range(1, max_retries + 2):  # attempt 1 = initial, 2+ = retries
        score, reason = compute_score(pipeline, input_text, current_output)
        best_score = max(best_score, score)

        console.print(
            f"  [dim]Optimizer attempt {attempt}: score={score:.1f}/10 "
            f"(threshold={threshold})[/dim]"
        )

        if score >= threshold:
            # Winner — save the prompt variant
            _log_optimizer(
                pipeline=pipeline,
                node=node_name,
                attempt=attempt,
                input_text=input_text,
                output_text=current_output,
                rule_score=rule_based_score(pipeline, current_output)[0] if rule_based_score(pipeline, current_output) else None,
                eval_score=score,
                final_score=score,
                action="accepted",
                prompt_before=prompt_template,
                prompt_after=current_prompt,
            )

            if current_prompt and current_prompt != prompt_template:
                ts = datetime.now(timezone.utc).isoformat()
                save_winning_prompt(pipeline, node_name, current_prompt, ts)
                console.print(f"  [green]Winning prompt variant saved for '{node_name}'[/green]")

            return current_output, score

        # Score below threshold — log and try mutation
        _log_optimizer(
            pipeline=pipeline,
            node=node_name,
            attempt=attempt,
            input_text=input_text,
            output_text=current_output,
            rule_score=rule_based_score(pipeline, current_output)[0] if rule_based_score(pipeline, current_output) else None,
            eval_score=score,
            final_score=score,
            action="retry" if attempt <= max_retries else "gave_up",
            prompt_before=current_prompt,
        )

        if attempt > max_retries:
            console.print(
                f"  [yellow]Optimizer exhausted {max_retries} retries — "
                f"best score={best_score:.1f}[/yellow]"
            )
            break

        # Mutate prompt and rerun
        console.print(f"  [dim]Mutating prompt for '{node_name}'...[/dim]")
        new_prompt = mutate_prompt(current_prompt, input_text, current_output, score, reason)

        if new_prompt and new_prompt != current_prompt:
            node_instance.prompt_template = new_prompt
            current_prompt = new_prompt
            try:
                retry_result = node_instance.run(
                    pipeline=pipeline,
                    node=f"{node_name}_retry{attempt}",
                    input_text=input_text,
                )
                current_output = retry_result.output
            except Exception as exc:
                console.print(f"  [red]Retry {attempt} failed: {exc}[/red]")
                break
        else:
            console.print("  [dim]Mutation produced no change — stopping[/dim]")
            break

    return current_output, best_score
