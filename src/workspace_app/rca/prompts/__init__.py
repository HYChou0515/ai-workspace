"""Loader for the RCA agent system prompt."""

from __future__ import annotations

from importlib.resources import files


def load_system_prompt() -> str:
    """Read the RCA agent's system prompt from the package resource.

    Lives as a markdown file so it's editable without recompiling and
    diff-friendly in PRs."""
    return (files("workspace_app.rca.prompts") / "system.md").read_text("utf-8")
