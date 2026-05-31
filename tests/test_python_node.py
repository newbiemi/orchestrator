"""
TDD tests for PythonNode — written BEFORE the implementation.
Run: pytest tests/test_python_node.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make sure orchestrator root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_node(fn_dotted: str, **kwargs):
    """Import PythonNode and construct it from a dotted fn path."""
    from core.python_node import PythonNode
    return PythonNode(fn=fn_dotted, **kwargs)


# ── registered callables used in tests ───────────────────────────────────────

def _fn_returns_dict(input_text: str) -> dict:
    return {"status": "ok", "length": len(input_text)}


def _fn_returns_str(input_text: str) -> str:
    return f"processed: {input_text[:10]}"


def _fn_raises(input_text: str):
    raise ValueError("boom — deliberate failure")


# Register them under dotted paths the tests use
import tests.test_python_node as _self_module
_REGISTRY_PATCH = {
    "tests.test_python_node._fn_returns_dict": _fn_returns_dict,
    "tests.test_python_node._fn_returns_str":  _fn_returns_str,
    "tests.test_python_node._fn_raises":        _fn_raises,
}


# ── tests ─────────────────────────────────────────────────────────────────────

class TestPythonNodeInit:
    def test_model_attribute_is_python(self):
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_returns_str")
        assert node.model == "python"

    def test_accepts_ignored_node_kwargs(self):
        """Must silently accept the same kwargs _node_from_config might pass."""
        from core.python_node import PythonNode
        # Should not raise even though these make no sense for Python nodes
        node = PythonNode(
            fn="tests.test_python_node._fn_returns_str",
            model="ignored-model",
            system="You are a helpful assistant.",
            temperature=0.7,
            prompt_template="some template",
        )
        assert node.model == "python"


class TestPythonNodeRun:
    """Core contract: PythonNode.run() returns a valid NodeResult."""

    def test_dict_result_is_json_serialized(self):
        """When fn returns a dict, NodeResult.output is its JSON string."""
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_returns_dict")
        with patch("core.python_node.log_run"):
            result = node.run(pipeline="test", node="n1", input_text="hello world")
        parsed = json.loads(result.output)
        assert parsed == {"status": "ok", "length": 11}

    def test_str_result_passes_through(self):
        """When fn returns a str, NodeResult.output is that string directly."""
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_returns_str")
        with patch("core.python_node.log_run"):
            result = node.run(pipeline="test", node="n1", input_text="abcdefghij extra")
        assert result.output == "processed: abcdefghij"

    def test_exception_produces_error_noderesult(self):
        """When fn raises, output starts with [ERROR] and the run still returns a NodeResult."""
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_raises")
        with patch("core.python_node.log_run"):
            result = node.run(pipeline="test", node="n1", input_text="anything")
        assert result.output.startswith("[ERROR]")
        assert "boom" in result.output

    def test_noderesult_fields(self):
        """NodeResult must have the standard fields the pipeline depends on."""
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_returns_str")
        with patch("core.python_node.log_run"):
            result = node.run(pipeline="p1", node="mynode", input_text="x")
        assert result.model == "python"
        assert result.token_count == 0
        assert result.pipeline == "p1"
        assert result.node == "mynode"
        assert isinstance(result.latency_ms, int)

    def test_log_run_called_once(self):
        """log_run must be called exactly once per node execution."""
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_returns_str")
        with patch("core.python_node.log_run") as mock_log:
            node.run(pipeline="p", node="n", input_text="hi")
        mock_log.assert_called_once()

    def test_log_run_called_on_exception(self):
        """log_run must also be called when fn raises (so the DB records the failure)."""
        from core.python_node import PythonNode
        node = PythonNode(fn="tests.test_python_node._fn_raises")
        with patch("core.python_node.log_run") as mock_log:
            node.run(pipeline="p", node="n", input_text="hi")
        mock_log.assert_called_once()


class TestPythonNodeDispatch:
    """_node_from_config must return a PythonNode when type == 'python'."""

    def test_node_from_config_returns_python_node(self):
        from core.pipeline import _node_from_config
        from core.python_node import PythonNode
        cfg = {
            "name": "my_algo",
            "type": "python",
            "fn": "tests.test_python_node._fn_returns_str",
        }
        name, node = _node_from_config(cfg)
        assert name == "my_algo"
        assert isinstance(node, PythonNode)
