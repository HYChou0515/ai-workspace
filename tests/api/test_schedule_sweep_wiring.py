"""The schedule sweep reads the durable snapshot, never the live sandbox.

`files.read` routes warm-first. On the hosted backend that probe is also the
RECOVERY trigger: a handle the address store still resolves, plus a sandbox the
reaper has taken away, means the read REBUILDS the sandbox (create + restore +
mark_ready). That is correct for a person opening a file — they are about to use
it — and wrong for a sweep, which would then resurrect the sandbox of every item
that has a `schedules.json`, once per tick, on every pod. The idle reaper would
be permanently undone for exactly those items, and nothing would say so.

The durable store answers the same question without waking anything. It lags the
live sandbox by at most one mirror interval (5s by default) — which for a
declaration that fires on the hour is no lag at all.

A source-text check, deliberately: what it guards is a WIRING choice that looks
like a harmless simplification from up close ("read is read"), and the failure it
prevents shows up as sandbox capacity, not as a test.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "src" / "workspace_app" / "api" / "app.py"


def test_the_sweep_does_not_read_through_the_live_sandbox() -> None:
    source = _APP.read_text(encoding="utf-8")

    call = re.search(r"UserScheduleSweeper\((.*?)\n        \)", source, re.DOTALL)
    assert call is not None, "the sweeper is no longer built here — move this guard with it"

    read_arg = re.search(r"\bread=([\w.]+)", call.group(1))
    assert read_arg is not None, "the sweeper is built without a read"
    assert read_arg.group(1) != "files.read", (
        "the schedule sweep reads through the facade, which is warm-first — on the "
        "hosted backend that rebuilds any sandbox the reaper took away, every tick, "
        "for every item that has schedules"
    )

    # And the OTHER half. Reading the snapshot alone is not safe: the snapshot
    # lags the workspace, so a page's just-saved file reads as missing — and
    # missing is what unregisters a schedule, permanently. The live read is how
    # a deletion is confirmed before it counts, so a sweep wired without it
    # trades sandbox churn for silent data loss.
    live_arg = re.search(r"read_live=([\w.]+)", call.group(1))
    assert live_arg is not None, (
        "the sweeper has no live read, so it cannot tell a lagging mirror from a "
        "deleted file — and it will unregister schedules that still exist"
    )
    assert live_arg.group(1) == "files.read", (
        "the confirming read must reach the LIVE workspace; the snapshot cannot "
        "confirm anything about itself"
    )


def test_a_scheduled_run_gets_its_own_conversation() -> None:
    """`_start_page_schedule` must open a chat, like the interactive entrance.

    Without a `chat_id` the run keys on the item id; `workflow_exec.drive_turn`
    looks that up, finds no conversation, and falls back to the item's DEFAULT
    chat — so the run reads the user's own history as context and appends its
    turns there. That was P22's headline finding, fixed on the entrance somebody
    is watching and left standing on the one that fires at 3am.

    A source check for the same reason as the reads above: what it pins is a
    WIRING choice whose failure is invisible until somebody opens their chat and
    finds a conversation they did not have.
    """
    source = _APP.read_text(encoding="utf-8")

    body = source.split("async def _start_page_schedule", 1)[-1].split("\n    lifespan", 1)[0]

    assert "open_run_chat" in body, "a scheduled run does not open its own conversation"
    assert "chat_id=chat_id" in body, "it opens one and then does not use it"
    assert "settle_run_chat" in body, "the chat is never linked to its run, or cleaned up"
