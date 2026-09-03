"""Every `server.*` knob must actually reach the composition root.

A configuration field that no builder reads is worse than a missing feature: it
is documented, an operator sets it, nothing happens, and there is no error to
follow. The dataclass is the single source of truth for what a deploy can tune,
so adding a field there without consuming it in `__main__` is the defect this
test names.

Deliberately a source-text check rather than a per-field assertion: the point is
to cover fields that do not exist yet, so a future knob is caught the day it is
added instead of the day someone notices it does nothing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from workspace_app.config.schema import HistorySettings, ServerSettings

_COMPOSITION_ROOT = Path(__file__).resolve().parents[2] / "src" / "workspace_app" / "__main__.py"


def test_every_server_setting_is_read_by_the_composition_root():
    source = _COMPOSITION_ROOT.read_text(encoding="utf-8")

    unread = [
        f.name
        for f in dataclasses.fields(ServerSettings)
        if f"settings.server.{f.name}" not in source
    ]

    assert not unread, (
        f"{unread} exist in ServerSettings but nothing in __main__.py reads them — "
        "an operator can set them and nothing will happen"
    )


def test_every_history_setting_is_read_by_the_composition_root():
    """Same rule, same reason — and this section is where it bites hardest.

    Every knob here governs how much of a conversation survives, and getting
    none of it is silent by design: an unresolved ceiling means "send it all",
    which looks exactly like a working deploy right up until a thread grows too
    large. So a `history` field nothing reads does not fail, it just quietly
    never applies."""
    source = _COMPOSITION_ROOT.read_text(encoding="utf-8")

    unread = [
        f.name
        for f in dataclasses.fields(HistorySettings)
        if f"settings.history.{f.name}" not in source
    ]

    assert not unread, (
        f"{unread} exist in HistorySettings but nothing in __main__.py reads them — "
        "an operator can set them and nothing will happen"
    )
