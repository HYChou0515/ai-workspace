"""The shipped `app.json` manifests list `agent.tools` and `agent.skills` ONE
entry per line.

A diff of a manifest is read by a person deciding what an App may do, and a
grant is a line of that diff: with six names packed on one line, adding or
removing one shows as a rewrite of the whole line and the reviewer has to
diff it by eye. One per line makes every grant its own `+`/`-`.

A layout rule, guarded here because nothing else can see it: the loader
parses the JSON and cannot tell how it was laid out, and a manifest edited by
hand drifts back to packed lines the moment somebody appends to the end of
one. The check is on the RAW text, on purpose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "src" / "workspace_app" / "apps"
MANIFESTS = sorted(APPS.glob("*/app.json"))
LISTS = ("tools", "skills")

#: One entry on a line: optional indent, one quoted string, an optional comma.
ONE_ENTRY = re.compile(r'^\s*"[^"]+",?\s*$')


def _array_lines(text: str, key: str) -> list[str]:
    """The raw lines BETWEEN `"key": [` and its closing `]`, or `[]` when the
    manifest has no such key. The key is looked up under `agent`, which is
    where the loader reads it."""
    m = re.search(rf'^\s*"{key}":\s*\[(.*?)^\s*\]', text, re.S | re.M)
    if m is None:
        return []
    return [line for line in m.group(1).splitlines() if line.strip()]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("key", LISTS)
def test_agent_list_is_one_entry_per_line(manifest: Path, key: str) -> None:
    text = manifest.read_text(encoding="utf-8")
    entries = json.loads(text)["agent"].get(key, [])
    lines = _array_lines(text, key)
    packed = [line.strip() for line in lines if not ONE_ENTRY.match(line)]
    where = f"{manifest.parent.name}/app.json `{key}`"
    assert not packed, f"{where}: more than one entry on a line:\n  " + "\n  ".join(packed)
    # And the line-per-entry layout accounts for every entry the loader sees —
    # a `["a", "b"]` on the `"key": [` line itself would leave zero lines
    # between the brackets and pass the check above with nothing checked.
    assert len(lines) == len(entries), f"{where}: {len(entries)} entries, {len(lines)} lines"


def test_the_guard_sees_every_shipped_manifest() -> None:
    # A glob that silently matches nothing is a guard that guards nothing.
    shipped = {"rca", "pm", "playground", "topic-hub", "_template"}
    assert {p.parent.name for p in MANIFESTS} >= shipped
