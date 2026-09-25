"""The pager's launch commands (plan-view-plugins-pr4 P4), dispatched by
``chart_view.cli``. Standard library only: they run on every scroll.

- ``facet_build {"path", "rev"}`` -- build or reuse the cache (``build_command``)
  for the view file at ``path``, as ``query`` reads it (``rev`` is the
  gallery's digest of the text, ignored here); ``{"spec"}`` carries the text
  instead, for a view with no file;
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

#: How ``query`` and ``facet_build`` are handed a view (#847/#848 P9): its file
#: (with the renderer's ``rev``, a digest of the text it holds, ignored here --
#: it makes an edited file a new call), or its text, for a view with no file.
VIEW_ARGUMENTS: dict[str, Any] = {
    "path": {"type": "string", "description": "The .ai.yaml file, relative to the workspace."},
    "rev": {"type": "string", "description": "The renderer's digest of the text; ignored."},
    "spec": {"type": "string", "description": "The chart file's YAML text, if there is no file."},
}
VIEW_FORMS = (frozenset({"path"}), frozenset({"path", "rev"}), frozenset({"spec"}))


def forms_schema(
    props: dict[str, Any], forms: tuple[frozenset[str], ...], optional: frozenset[str]
) -> dict[str, Any]:
    """The JSON schema of a call that takes exactly one of `forms` (plus any of
    `optional`): what the command's own check accepts, and nothing else."""
    return {
        "type": "object",
        "properties": props,
        "oneOf": [
            {"required": sorted(f), "propertyNames": {"enum": sorted(f | optional)}} for f in forms
        ],
    }


COMMANDS: dict[str, dict[str, Any]] = {
    "facet_build": {
        "description": (
            "Build (or reuse) the cache a facet: spec opens as a gallery; answer its key and build."
        ),
        "properties": VIEW_ARGUMENTS,
        "forms": VIEW_FORMS,
    },
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


# Every facet command also takes an optional integer `epoch`, which the
# sandbox ignores: the gallery bumps it to retry a failed chain as NEW queries
# (the args are the query key; a cached failure would otherwise stay cached).
_EPOCH = {"type": "integer", "description": "The gallery's retry epoch; ignored here."}


def schema(name: str) -> dict[str, Any]:
    props = COMMANDS[name]["properties"]
    forms = COMMANDS[name].get("forms", (frozenset(props),))
    return forms_schema({**props, "epoch": _EPOCH}, forms, frozenset({"epoch"}))


def _root() -> Path:
    return Path.home() / ".cache" / "views"


def _args(name: str, raw: str) -> dict[str, Any]:
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as e:  # nested past the decoder
        raise ValueError(f"argument is not JSON this command can read: {e}") from None
    forms = COMMANDS[name].get("forms", (frozenset(COMMANDS[name]["properties"]),))
    if not isinstance(args, dict) or set(args) - {"epoch"} not in forms:
        takes = " or ".join(str(sorted(f)) for f in forms)
        raise ValueError(f"{name} takes exactly {takes} (and an optional epoch)")
    if "epoch" in args and type(args.pop("epoch")) is not int:
        raise ValueError("epoch must be an integer")
    # Checked here, before the cache is looked for: a wrong call must be 2 even
    # with no cache yet, not 3 ("build it"), which would build for nothing.
    if "positions" in args and not (
        isinstance(args["positions"], list) and all(type(p) is int for p in args["positions"])
    ):
        raise ValueError("positions must be a list of integer group positions")
    if "position" in args and type(args["position"]) is not int:
        raise ValueError("position must be an integer group position")
    for text in ("key", "build", "spec", "path", "rev"):
        # a build that is not text would read as "rebuilt since" (exit 4) and
        # send the gallery round a refetch loop instead of naming the bad call
        if text in args and not isinstance(args[text], str):
            raise ValueError(f"{text} must be a string")
    return args


def run(name: str, raw: str) -> int:
    try:
        args = _args(name, raw)
        if name == "facet_build":
            # pandas lives behind this import, so only a build pays for it
            from chart_view.cli import read_view
            from chart_view.facet.build_command import run as build

            text = args["spec"] if "spec" in args else read_view(args["path"])
            return 2 if text is None else build(text)
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
