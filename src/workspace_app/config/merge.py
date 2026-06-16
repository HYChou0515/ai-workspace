"""Layered config merge — the core merge function the loader applies twice.

1. Bundled defaults ◇ operator's `config.yaml` (so the operator only
   writes the deltas — Q7: "全可設定但保留預設值").
2. Preset config ◇ usage entry (`workspace_chat[]`, `kb_chat`,
   template `_config.json`) — usage references a preset by name and
   overrides only what differs (Q5).

Rules (applied at every nesting level):

- **Dict** ◇ **dict** → recurse into per-key merge (deep, keys-only-in-
  base survive).
- **List** ◇ **list** → override REPLACES (chosen over append/dedup so
  a template can subtract a tool from its preset's defaults — Q5
  rationale).
- Anything else (scalar, type-mismatch dict↔list↔scalar, explicit
  `None`) → override wins verbatim.

Pure function: result is a fresh deep copy; mutating it can't touch
either input.
"""

from __future__ import annotations

import copy
from typing import Any


def merge_layered(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a fresh dict = `base` with `override` layered on top.

    Both inputs survive the call intact — the returned dict is a deep
    copy that callers may mutate freely."""
    out = copy.deepcopy(base)
    _apply(out, override)
    return out


def _apply(target: dict[str, Any], override: dict[str, Any]) -> None:
    """In-place: merge `override` into `target`. Recursive on dict-↓-dict
    pairs; everything else is a clean replace."""
    for key, over_val in override.items():
        base_val = target.get(key, _MISSING)
        if isinstance(base_val, dict) and isinstance(over_val, dict):
            # Both sides are dicts → recurse so nested keys survive when
            # not mentioned by override (the "shallow merge per key" rule
            # is really "deep merge while both are dicts; replace once
            # either side stops being a dict").
            merged = copy.deepcopy(base_val)
            _apply(merged, over_val)
            target[key] = merged
        else:
            # Scalar / list / type-mismatch / new key → override wins
            # verbatim. Deep-copied so the result owns its own tree.
            target[key] = copy.deepcopy(over_val)


_MISSING: Any = object()
