"""Render an entity skeleton into a concrete file (#419 §D).

The skeleton is a markdown file with a closed placeholder vocabulary —
`{{number}}`, `{{arg.x}}`, `{{arg.x?}}` (optional), `{{now}}`, `{{actor}}`.
Only variable substitution and optional omission; no conditionals, loops, or
expressions. This is the one code path all three create routes converge on.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def render_skeleton(
    template: str, args: dict[str, Any], *, number: int, now: str, actor: str
) -> str:
    def repl(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if token == "number":
            return str(number)
        if token == "now":
            return now
        if token == "actor":
            return actor
        if token.startswith("arg."):
            key = token[4:].removesuffix("?")
            return str(args.get(key, ""))
        return match.group(0)

    return _PLACEHOLDER.sub(repl, template)
