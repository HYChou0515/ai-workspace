"""The pager's launch commands (plan-view-plugins-pr4 P4), dispatched by
``chart_view.cli``. Standard library only: they run on every scroll.

- ``facet_index {"key"}`` -- the index a gallery sorts and marks from;
- ``facet_page {"key", "build", "positions"}`` -- records at sorted positions;
- ``facet_exact {"key", "build", "position"}`` -- one group's exact values.

The cache lives in ``~/.cache/views``: the isolated launcher sets HOME to the
sandbox's ``.home``, the per-sandbox infra area (Q12).

Exit codes: 0 answer on stdout; 2 a wrong call; 3 the cache cannot be used,
build it again; 4 the cache was rebuilt since the index the call names,
refetch the index.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chart_view.facet import CacheUnusable
from chart_view.facet.pager import StaleIndex, exact_payload, index_payload, page_payload

UNUSABLE = 3
STALE = 4

_KEY = {"type": "string", "description": "The cache digest the build returned."}
_BUILD = {"type": "string", "description": "The build id of the index the call sorted from."}

COMMANDS: dict[str, dict[str, Any]] = {
    "facet_index": {
        "description": (
            "A facet cache's index: scale, facet columns, layout, each group's key and sort values."
        ),
        "properties": {"key": _KEY},
    },
    "facet_page": {
        "description": (
            "The records at the given positions of a facet cache, as the chart's wire columns."
        ),
        "properties": {
            "key": _KEY,
            "build": _BUILD,
            "positions": {"type": "array", "items": {"type": "integer"}},
        },
    },
    "facet_exact": {
        "description": "One group's exact values from a facet cache, as an f64 wire column.",
        "properties": {"key": _KEY, "build": _BUILD, "position": {"type": "integer"}},
    },
}


def schema(name: str) -> dict[str, Any]:
    props = COMMANDS[name]["properties"]
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _root() -> Path:
    return Path.home() / ".cache" / "views"


def _args(name: str, raw: str) -> dict[str, Any]:
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as e:  # nested past the decoder
        raise ValueError(f"argument is not JSON this command can read: {e}") from None
    want = set(COMMANDS[name]["properties"])
    if not isinstance(args, dict) or set(args) != want:
        raise ValueError(f"{name} takes exactly {sorted(want)}")
    # Checked here, before the cache is looked for: a wrong call must be 2 even
    # with no cache yet, not 3 ("build it"), which would build for nothing.
    if "positions" in args and not (
        isinstance(args["positions"], list) and all(type(p) is int for p in args["positions"])
    ):
        raise ValueError("positions must be a list of integer group positions")
    if "position" in args and type(args["position"]) is not int:
        raise ValueError("position must be an integer group position")
    for text in ("key", "build"):
        # a build that is not text would read as "rebuilt since" (exit 4) and
        # send the gallery round a refetch loop instead of naming the bad call
        if text in args and not isinstance(args[text], str):
            raise ValueError(f"{text} must be a string")
    return args


def run(name: str, raw: str) -> int:
    try:
        args = _args(name, raw)
        if name == "facet_index":
            answer = index_payload(_root(), args["key"])
        elif name == "facet_page":
            answer = page_payload(_root(), args["key"], args["build"], args["positions"])
        else:
            answer = exact_payload(_root(), args["key"], args["build"], args["position"])
    except StaleIndex as e:
        print(str(e), file=sys.stderr)
        return STALE
    except CacheUnusable as e:
        print(str(e), file=sys.stderr)
        return UNUSABLE
    except (ValueError, TypeError, IndexError) as e:  # a wrong call
        print(str(e), file=sys.stderr)
        return 2
    json.dump(answer, sys.stdout, separators=(",", ":"))
    return 0
