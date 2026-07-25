"""Tier routing + the algorithmic quality gate.

The load-bearing case is `test_absent_tier_returns_plain_claude_node`: `tier` is
opt-in, and every pipeline that predates it must keep behaving exactly as before.
"""
import json

import pytest

from core.claude_node import ClaudeNode
from core.pipeline import _node_from_config
from core.routed_node import GateFailure, RoutedNode, check_gate


# ── gate ───────────────────────────────────────────────────────────────────────

def test_empty_output_always_fails():
    with pytest.raises(GateFailure, match="empty"):
        check_gate("   ", {})


def test_no_gate_config_accepts_any_nonempty_text():
    check_gate("anything at all", {})


def test_min_chars_enforced():
    with pytest.raises(GateFailure, match="min_chars"):
        check_gate("short", {"min_chars": 100})
    check_gate("x" * 100, {"min_chars": 100})


def test_max_chars_enforced():
    with pytest.raises(GateFailure, match="max_chars"):
        check_gate("x" * 101, {"max_chars": 100})


def test_require_json_rejects_prose():
    with pytest.raises(GateFailure, match="not valid JSON"):
        check_gate("Sure! Here is your answer.", {"require_json": True})


def test_require_json_accepts_fenced_json():
    """Free models routinely wrap JSON in a markdown fence — that is a
    formatting quirk, not a quality failure."""
    check_gate('```json\n{"a": 1}\n```', {"require_json": True})


def test_require_keys_enforced():
    with pytest.raises(GateFailure, match="missing required key"):
        check_gate(json.dumps({"a": 1}), {"require_keys": ["a", "b"]})
    check_gate(json.dumps({"a": 1, "b": 2}), {"require_keys": ["a", "b"]})


def test_forbid_patterns_catches_refusals():
    with pytest.raises(GateFailure, match="forbidden pattern"):
        check_gate("I cannot help with that request.", {"forbid_patterns": ["I cannot"]})


# ── node construction ──────────────────────────────────────────────────────────

def test_absent_tier_returns_plain_claude_node():
    """Backward compatibility: no `tier` key means nothing changes."""
    name, node = _node_from_config({"name": "decision", "type": "claude", "system": "s"})
    assert isinstance(node, ClaudeNode)
    assert not isinstance(node, RoutedNode)


def test_explicit_claude_tier_returns_plain_claude_node():
    _, node = _node_from_config({"name": "d", "type": "claude", "system": "s", "tier": "claude"})
    assert isinstance(node, ClaudeNode)


def test_free_tier_returns_routed_node():
    _, node = _node_from_config(
        {"name": "d", "type": "claude", "system": "s", "tier": "free", "task_type": "summarize"}
    )
    assert isinstance(node, RoutedNode)
    assert node.tier == "free"
    assert node.task_type == "summarize"


def test_unknown_tier_is_rejected_at_build_time():
    with pytest.raises(ValueError, match="unknown tier"):
        _node_from_config({"name": "d", "type": "claude", "system": "s", "tier": "cheapest"})


def test_memory_context_passes_through_to_wrapped_node():
    """The pipeline injects memory by assigning node.memory_context; a
    RoutedNode must forward that to the ClaudeNode it wraps."""
    _, node = _node_from_config({"name": "d", "type": "claude", "system": "s", "tier": "free"})
    node.memory_context = "recalled facts"
    assert node.claude_node.memory_context == "recalled facts"


# ── escalation ─────────────────────────────────────────────────────────────────

def _routed(tier, gate=None):
    return RoutedNode(tier=tier, claude_node=ClaudeNode(system="s"), gate=gate)


def test_free_tier_does_not_escalate(monkeypatch):
    node = _routed("free")
    monkeypatch.setattr(node, "_run_free", lambda **kw: (_ for _ in ()).throw(GateFailure("bad")))
    monkeypatch.setattr(node.claude_node, "run", lambda **kw: pytest.fail("must not escalate"))
    with pytest.raises(GateFailure):
        node.run(pipeline="p", node="n", input_text="x")


def test_free_then_verify_escalates_on_gate_failure(monkeypatch):
    node = _routed("free-then-verify")
    monkeypatch.setattr(node, "_run_free", lambda **kw: (_ for _ in ()).throw(GateFailure("bad")))
    monkeypatch.setattr(node.claude_node, "run", lambda **kw: "escalated")
    assert node.run(pipeline="p", node="n", input_text="x") == "escalated"


def test_free_then_verify_keeps_passing_free_output(monkeypatch):
    node = _routed("free-then-verify")
    monkeypatch.setattr(node, "_run_free", lambda **kw: "free-result")
    monkeypatch.setattr(node.claude_node, "run", lambda **kw: pytest.fail("must not escalate"))
    assert node.run(pipeline="p", node="n", input_text="x") == "free-result"


def test_claude_tier_bypasses_free_chain(monkeypatch):
    node = _routed("claude")
    monkeypatch.setattr(node, "_run_free", lambda **kw: pytest.fail("must not call free chain"))
    monkeypatch.setattr(node.claude_node, "run", lambda **kw: "claude-result")
    assert node.run(pipeline="p", node="n", input_text="x") == "claude-result"
