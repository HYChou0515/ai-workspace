"""Every `server.*` knob must be named in the operator's manual.

`test_server_settings_are_wired.py` proves a knob REACHES the code. This proves
somebody can find out it exists. The two failures are different and both silent:
a knob nothing reads does nothing when set, and a knob nothing documents is
never set at all.

The second one has a sharper edge here, because `docs/migrations.md` cites
`configuration.md` as the reference for what a new option changes. A ledger row
pointing at a page that does not mention the option sends the reader somewhere
to find nothing — worse than saying nothing, because they will conclude they
misread the name.

Deliberately a source-text check over the dataclass, like its sibling: the point
is to cover fields that do not exist yet, so a future knob is caught the day it
is added rather than the day an operator needs it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from workspace_app.config.schema import ServerSettings

_MANUAL = Path(__file__).resolve().parents[2] / "docs" / "configuration.md"


def test_every_server_setting_is_named_in_the_manual():
    manual = _MANUAL.read_text(encoding="utf-8")

    undocumented = [f.name for f in dataclasses.fields(ServerSettings) if f.name not in manual]

    assert not undocumented, (
        f"{undocumented} are settable and undocumented — an operator cannot discover them, "
        "and docs/migrations.md points at this file for what they change"
    )
