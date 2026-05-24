"""Loader for the KB agent system prompt (markdown so it's editable without
recompiling and diff-friendly in PRs — mirrors workspace_app.rca.prompts)."""

from __future__ import annotations

from importlib.resources import files


def load_kb_system_prompt() -> str:
    return (files("workspace_app.kb.prompts") / "system.md").read_text("utf-8")
