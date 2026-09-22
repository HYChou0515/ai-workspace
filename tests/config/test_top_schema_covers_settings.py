"""`_TOP_SCHEMA` is a hand-written mirror of `Settings`'s sections, and nothing
was keeping the two in step.

The failure is quiet in the worst way: a new section lands on `Settings`, every
unit test that constructs `Settings(...)` directly passes, and the deployment
that puts the section in `config.yaml` is refused at boot with "unknown key" —
which reads as a typo, not as a missing loader entry. That is how this test came
to exist: adding `backup:` to a real config.yaml was refused while every test
around it was green.

Both directions matter. A section in `Settings` and not in `_TOP_SCHEMA` cannot
be configured at all; a key in `_TOP_SCHEMA` and not in `Settings` is accepted
from a config file and then silently dropped.
"""

from __future__ import annotations

import dataclasses

from workspace_app.config.loader import _TOP_SCHEMA
from workspace_app.config.schema import Settings

_SETTINGS_SECTIONS = frozenset(f.name for f in dataclasses.fields(Settings))


def test_every_settings_section_can_be_set_from_a_config_file():
    unconfigurable = sorted(_SETTINGS_SECTIONS - set(_TOP_SCHEMA))

    assert not unconfigurable, (
        f"{unconfigurable} exist on Settings but are not in _TOP_SCHEMA, so the loader "
        "refuses them as 'unknown key' — a deployment cannot set them at all, and the "
        "error names them as typos rather than as a gap here"
    )


def test_every_configurable_section_reaches_settings():
    dropped = sorted(set(_TOP_SCHEMA) - _SETTINGS_SECTIONS)

    assert not dropped, (
        f"{dropped} are accepted from a config file but have no home on Settings, so "
        "anything written under them is silently ignored"
    )
