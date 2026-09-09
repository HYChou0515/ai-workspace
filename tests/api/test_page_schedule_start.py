"""What `_start_page_schedule` DOES, driven rather than read.

The guards this replaces were source-text checks on `api/app.py`: they asserted
that the tokens `try:` and `except Exception:` appear around the post-start
call. That is not the property. Adding `raise` to the post-start handler — the
exact regression the guard is named after — left every one of 200 tests green,
because no test ever called the function.

So the function moved to module level and these drive it with doubles. The
wiring guard in `test_schedule_sweep_wiring.py` keeps `create_app` pointed at
it, so the extraction cannot be silently bypassed.
"""

from __future__ import annotations

from typing import Any

import pytest

from workspace_app.api.app import start_page_schedule


class _Locator:
    """Enough locator for this one function, with the failure seams exposed."""

    def __init__(self, *, ours: bool = True) -> None:
        self.ours = ours
        self.settled: list[tuple[str, str | None]] = []
        self.settle_raises_on: str | None = None  # "link" | "cleanup" | None

    def chat_for_schedule(self, item_id: str, title: str, key: str) -> tuple[str, bool]:
        return f"chat:{item_id}:{key}", self.ours

    def slug_of(self, item_id: str) -> str | None:
        return "app"

    def profile_of(self, item_id: str) -> str:
        return "default"

    def settle_run_chat(self, chat_id: str, run_id: str | None) -> None:
        self.settled.append((chat_id, run_id))
        which = "cleanup" if run_id is None else "link"
        if self.settle_raises_on == which:
            raise RuntimeError(f"specstar said no ({which})")


class _Orchestrator:
    def __init__(self, *, run_id: str = "run-1", boom: Exception | None = None) -> None:
        self.run_id = run_id
        self.boom = boom
        self.starts: list[dict[str, Any]] = []

    async def start(self, **kw: Any) -> str:
        self.starts.append(kw)
        if self.boom is not None:
            raise self.boom
        return self.run_id


async def _fire(loc: _Locator, orch: _Orchestrator) -> str | None:
    return await start_page_schedule(
        locator=loc,
        orchestrator=orch,
        item_id="i1",
        workflow_id="build-report",
        acting_user="alice",
        payload={"line": "A"},
        key="wui:i1:abcd",
    )


async def test_a_started_run_is_never_reported_as_not_started() -> None:
    """PAST THE POINT OF NO RETURN.

    Once `orchestrator.start` returns, the run exists and is already writing to
    the conversation. Linking the chat to it is bookkeeping. Raising here would
    tell the sweep "nothing started" — and the sweep's answer to that is to hand
    the window back and fire again. One failed `update` becomes two reports, two
    emails, twice.

    Driven, not read: the double's `settle_run_chat` raises on the LINK call,
    which is the only way to reach that handler.
    """
    loc = _Locator()
    loc.settle_raises_on = "link"
    orch = _Orchestrator(run_id="run-7")

    run_id = await _fire(loc, orch)

    assert run_id == "run-7", "a started run was reported as not started"
    assert len(orch.starts) == 1


async def test_a_run_that_never_started_is_reported_as_not_started() -> None:
    """The control, and the other half of the contract.

    `StartRun`'s docstring says raising means NOTHING STARTED, because the sweep
    hands the window back on a raise. So a start that genuinely failed MUST
    propagate — a test that only pinned "never raises" would be satisfied by a
    function that swallows everything and silently drops the schedule.
    """
    loc = _Locator()
    orch = _Orchestrator(boom=RuntimeError("no capacity"))

    with pytest.raises(RuntimeError, match="no capacity"):
        await _fire(loc, orch)


async def test_a_failed_start_takes_down_only_the_chat_this_call_made() -> None:
    """A chat the schedule has been using holds its history.

    Deleting it because one night's start failed loses every previous run's
    thread. But a chat THIS call minted and never linked is a free chat, and the
    earliest free chat is what the item opens as its default — so a schedule
    failing nightly would install a new default conversation every night.
    """
    ours = _Locator(ours=True)
    with pytest.raises(RuntimeError):
        await _fire(ours, _Orchestrator(boom=RuntimeError("x")))
    assert ours.settled == [("chat:i1:wui:i1:abcd", None)], "our own new chat was not cleaned up"

    theirs = _Locator(ours=False)
    with pytest.raises(RuntimeError):
        await _fire(theirs, _Orchestrator(boom=RuntimeError("x")))
    assert theirs.settled == [], "an existing chat was deleted, taking its history"


async def test_a_failing_cleanup_does_not_replace_the_failure_it_cleans_up_after() -> None:
    """The caller needs the original reason. A second error from the tidy-up
    buries it, and the sweep's log then names the wrong cause."""
    loc = _Locator()
    loc.settle_raises_on = "cleanup"

    with pytest.raises(RuntimeError, match="no capacity"):
        await _fire(loc, _Orchestrator(boom=RuntimeError("no capacity")))
