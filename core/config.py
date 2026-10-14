"""
Config loader — reads pipelines.yaml and returns typed dicts.
Single source of truth for all pipeline configuration.
"""
from pathlib import Path
from typing import Any, Optional

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config" / "pipelines.yaml"

_cache: dict = {}
_cache_mtime: float = 0.0


def _load_all() -> dict:
    global _cache, _cache_mtime
    mtime = CONFIG_PATH.stat().st_mtime
    if mtime != _cache_mtime:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _cache = yaml.safe_load(f) or {}
        _cache_mtime = mtime
    return _cache


def load_pipeline_config(name: str) -> dict:
    """
    Return the config block for a single pipeline.
    Raises ValueError if the pipeline name is not found.
    """
    all_cfg = _load_all()
    pipelines = all_cfg.get("pipelines", {})
    if name not in pipelines:
        available = ", ".join(pipelines.keys()) or "(none)"
        raise ValueError(
            f"Pipeline '{name}' not found in pipelines.yaml. "
            f"Available: {available}"
        )
    return pipelines[name]


def list_pipelines() -> list[str]:
    """Return all pipeline names defined in pipelines.yaml."""
    return list(_load_all().get("pipelines", {}).keys())


def get_node_configs(pipeline_name: str) -> list[dict]:
    """Return the ordered node list for a pipeline."""
    return load_pipeline_config(pipeline_name).get("nodes", [])


def get_optimizer_config(pipeline_name: str) -> dict:
    """Return prompt_optimizer block with defaults filled in."""
    cfg = load_pipeline_config(pipeline_name).get("prompt_optimizer", {})
    return {
        "enabled": cfg.get("enabled", False),
        "threshold": cfg.get("threshold", 7),
        "max_retries": cfg.get("max_retries", 2),
    }


def get_default_claude_model() -> str:
    """Return the default Claude model from pipelines.yaml `defaults.claude_model`."""
    try:
        return _load_all().get("defaults", {}).get("claude_model", "claude-sonnet-4-6")
    except Exception:
        return "claude-sonnet-4-6"


def get_memory_context_n(pipeline_name: str) -> int:
    """Return how many past runs to inject as memory context (Phase 7)."""
    return load_pipeline_config(pipeline_name).get("memory_context_n", 5)


def save_winning_prompt(pipeline_name: str, node_name: str, prompt: str, timestamp: str) -> None:
    """
    Append a winning prompt variant to pipelines.yaml (Phase 8).
    Variants are versioned — never overwritten.
    Uses ruamel.yaml to preserve comments and formatting.
    """
    from ruamel.yaml import YAML as RuamelYAML
    global _cache_mtime

    ry = RuamelYAML()
    ry.preserve_quotes = True
    with open(CONFIG_PATH, encoding="utf-8") as f:
        all_cfg = ry.load(f)

    node_list = all_cfg["pipelines"][pipeline_name].get("nodes", [])
    for node in node_list:
        if node["name"] == node_name:
            if "prompt_variants" not in node:
                node["prompt_variants"] = []
            node["prompt_variants"].append({"timestamp": timestamp, "prompt_template": prompt})
            break

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        ry.dump(all_cfg, f)

    # Invalidate cache so next read picks up the new file
    _cache_mtime = 0.0
