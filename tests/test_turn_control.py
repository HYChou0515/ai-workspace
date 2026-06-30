"""ITurnControl contract (#349) — the cross-pod turn epoch.

A turn stamps `my_epoch` when it starts; a watcher aborts it once the shared
epoch advances past that stamp. `advance` is how a new turn supersedes a prior
one (or an explicit Stop kills the running one); `current` is what the watcher
polls. The semantics must hold identically for every backend, so this file is a
backend-agnostic contract exercised against BOTH `ITurnControl` impls — the
in-memory one (single-pod / tests) and the specstar-backed one (multi-pod).
"""

from __future__ import annotations

import pytest

from workspace_app.resources import make_spec
from workspace_app.turn_control import InMemoryTurnControl
from workspace_app.turn_control.specstar_impl import SpecstarTurnControl


@pytest.fixture(params=["memory", "specstar"])
def control(request):
    if request.param == "memory":
        return InMemoryTurnControl()
    return SpecstarTurnControl(make_spec(default_user="u"))


async def test_advance_bumps_epoch_monotonically_and_current_reflects_it(control):
    assert await control.current("k") == 0  # fresh key: no turn has started
    assert await control.advance("k") == 1  # first turn / supersede
    assert await control.current("k") == 1
    assert await control.advance("k") == 2  # next supersede
    assert await control.current("k") == 2


async def test_epochs_are_independent_per_key(control):
    await control.advance("a")
    await control.advance("a")
    assert await control.current("a") == 2
    assert await control.current("b") == 0  # untouched key unaffected
    assert await control.advance("b") == 1
