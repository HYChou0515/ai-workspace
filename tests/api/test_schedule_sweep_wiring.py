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

    assert "chat_for_schedule" in body, "a scheduled run does not resolve its own conversation"
    assert "chat_id=chat_id" in body, "it resolves one and then does not use it"
    assert "settle_run_chat" in body, "the chat is never linked to its run, or cleaned up"

    # REUSED, not opened fresh. `open_run_chat` mints a new conversation every
    # call, which is right for a click and wrong for a schedule: the chat is what
    # `active_run_for_chat` collides on, so a new id per fire switches the
    # one-run rule off for the one entrance that repeats — and leaves a permanent
    # conversation behind each time (`every: minutes, n: 1` is 1440 a day).
    assert "open_run_chat" not in body, (
        "the scheduled entrance mints a fresh chat per fire; it must reuse the schedule's own"
    )
    # And only a chat this call created may be cleaned up: deleting one the
    # schedule has been using throws away every previous run's thread.
    assert "if ours:" in body, "the failure path deletes the schedule's chat unconditionally"


def test_a_started_run_is_never_reported_as_not_started() -> None:
    """Once `orchestrator.start` returns, the run EXISTS. Anything that fails
    after that is bookkeeping, and it must not reach the sweep as an exception.

    The sweep claims a window before asking for the run, so a raise is its
    signal to hand that window back and fire again — and it cannot tell "nothing
    started" from "it started and the chat link failed". The second fire opens a
    fresh chat id, so `active_run_for_chat` collides with nothing and the person
    gets two of whatever the workflow sends. Probed at three runs for one daily
    window before this guard existed.

    A source check on CONTROL FLOW, which is the thing at stake: the linking call
    has to sit under a `try` whose handler swallows.
    """
    source = _APP.read_text(encoding="utf-8")
    body = source.split("async def _start_page_schedule", 1)[-1].split("\n    lifespan", 1)[0]

    marker = "locator.settle_run_chat, chat_id, run_id"
    assert marker in body, "the run's chat is never linked"

    before = body.split(marker, 1)[0]
    # The `try:` is the last statement opener before the call; the call itself
    # may be wrapped across lines by the formatter, so look for the nearest one.
    opens = [ln.strip() for ln in before.splitlines() if ln.strip()]
    guard = "try:" if "try:" in opens[-3:] else opens[-1]
    assert guard == "try:", (
        "the post-start link is unguarded — a failure there tells the sweep the "
        f"run never started, and it fires the window again (last line was {guard!r})"
    )

    after = body.split(marker, 1)[1]
    assert "except Exception:" in after.split("return run_id", 1)[0], (
        "nothing catches a failure after the run exists"
    )


def test_the_fire_path_does_not_hold_the_event_loop() -> None:
    """`_start_page_schedule` runs INSIDE the sweep's tick, so its blocking calls
    hold the loop exactly as the sweep's own would.

    Every one of them is specstar I/O, and `chat_for_schedule` is seven round
    trips on its own — `item_conversation_mirror` asks every registered app model
    for its meta. The sweep offloads all of its own store calls and then handed
    the loop to this, which the sweep's guard could not see: it injects an
    in-memory `start` double, so it measures everything except the callback that
    actually fires.

    A source check, like its siblings here, because the failure is a WIRING
    choice whose symptom is latency on unrelated requests — nothing in this
    process fails, and nothing local reproduces it.
    """
    source = _APP.read_text(encoding="utf-8")
    body = source.split("async def _start_page_schedule", 1)[-1].split("\n    lifespan", 1)[0]

    blocking = ("chat_for_schedule", "settle_run_chat", "slug_of", "profile_of")
    # Matched on the pair, not on one spelling: the formatter wraps a long call
    # so `to_thread(locator.x` and `to_thread(\n    locator.x` are the same thing,
    # and a guard that only knows one of them passes the day black reflows it.
    flat = " ".join(body.split())
    unoffloaded = [
        name
        for name in blocking
        if f"locator.{name}" in flat
        and f"asyncio.to_thread( locator.{name}" not in flat
        and f"asyncio.to_thread(locator.{name}" not in flat
    ]

    assert not unoffloaded, (
        f"{unoffloaded} are blocking specstar calls made directly on the event loop, "
        "inside a sweep that offloads every one of its own"
    )


def test_both_write_boundaries_feed_the_index() -> None:
    """The facade is one of TWO ways bytes reach the durable store.

    A file written inside the sandbox — an agent's `exec`, a workflow's shell
    step — arrives through the mirror, which writes to the FileStore directly and
    never touches `WorkspaceFiles`. So a `schedules.json` produced that way was
    never indexed: the schedules never ran, and nothing said why. That is the
    same silent failure the index exists to prevent, entering through the door
    the facade fix did not cover.

    One callback wired to both, so they cannot disagree about what counts.
    """
    source = _APP.read_text(encoding="utf-8")

    facade = re.search(r"WorkspaceFiles\((.*?)\n    \)", source, re.DOTALL)
    mirror = re.search(r"SandboxSync\((.*?)\n    \)", source, re.DOTALL)
    assert facade is not None and mirror is not None, "one of the two is no longer built here"

    for name, call in (("facade", facade.group(1)), ("mirror", mirror.group(1))):
        assert "_note_schedule_file" in call, (
            f"the {name} write path does not tell the schedule index, so a file "
            "that arrives that way is never swept"
        )
