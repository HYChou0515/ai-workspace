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

_SRC = Path(__file__).resolve().parents[2] / "src" / "workspace_app"
_COMPOSITION_ROOT = _SRC / "__main__.py"
_CREATE_APP = _SRC / "api" / "app.py"


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


def test_every_history_setting_survives_the_SECOND_hop_as_well():
    """`__main__` reading a knob is only half the chain.

    It forwards each one to `create_app`, which forwards it again to the
    builder that actually applies it. The check above cannot see that second
    hop, so deleting the kwarg inside `create_app` leaves an operator's setting
    read, passed, and then dropped — with every test green, including the one
    written to catch exactly this. That is not hypothetical: it is what the
    adversarial review found for `max_tokens_window_ratio`.

    A source-text check for the same reason as its neighbour: it has to cover
    the field nobody has added yet, so a future knob is caught the day it is
    written rather than the day someone notices it does nothing.
    """
    import re

    main = _COMPOSITION_ROOT.read_text(encoding="utf-8")
    app = _CREATE_APP.read_text(encoding="utf-8")

    dropped = []
    for f in dataclasses.fields(HistorySettings):
        m = re.search(rf"(\w+)=settings\.history\.{re.escape(f.name)}\b", main)
        if m is None:
            continue  # the first test already names an unread field
        kwarg = m.group(1)
        # It has to arrive AND leave: `create_app` must accept the parameter and
        # pass it on, not merely have a name that looks like it.
        if f"{kwarg}=" not in app or app.count(f"{kwarg}") < 2:
            dropped.append(kwarg)

    assert not dropped, (
        f"{dropped} reach create_app from __main__.py but are not forwarded on inside it — "
        "the operator sets them, they travel one hop, and nothing applies them"
    )
