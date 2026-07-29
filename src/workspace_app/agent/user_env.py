"""Render an item's user-set environment variables into the file the tool
launchers read.

The format is deliberately the dumbest one that works: one ``KEY=VALUE`` line,
read back by a loop in the launcher (``prebuild.py``) that ``export``s each line
as a single word. It is NOT a `.env` that gets ``source``d — sourcing would make
import/export symmetric for free, but the shell then rewrites any value holding
``$``, a backtick or ``$(…)``, and the tool receives a key subtly different from
the one the user typed, with nothing anywhere pointing at the cause.

Because the format is line-oriented, a name or value carrying a newline could
inject an assignment the user never made. A text input cannot produce one, but
the item's PATCH route is reachable without the UI, so the check lives here —
at the one place that turns the record into the file — rather than being spread
across every caller.
"""

from __future__ import annotations

# What a name or value may not contain. `\n` and `\r` end a line, so either one
# would smuggle a second assignment; `=` in a NAME would silently rename the
# variable (`A=B=1` reads back as `A`).
_FORBIDDEN_IN_VALUE = ("\n", "\r")
_FORBIDDEN_IN_NAME = ("\n", "\r", "=")


def render_user_env(env_vars: dict[str, str]) -> str:
    """The item's variables as ``KEY=VALUE`` lines, ready to be written into the
    sandbox's infra area.

    Values are written VERBATIM — no quoting, no escaping. The launcher exports
    each line as one word, so quoting here would put the quotes *into* the value.

    An entry that cannot be expressed on one line is dropped rather than
    mangled: a key silently corrupted is worse than a key that is plainly
    absent, because the first fails inside the tool with no trail back."""
    lines = []
    for name, value in env_vars.items():
        if not name or any(c in name for c in _FORBIDDEN_IN_NAME):
            continue
        if any(c in value for c in _FORBIDDEN_IN_VALUE):
            continue
        lines.append(f"{name}={value}")
    return "".join(f"{line}\n" for line in lines)
