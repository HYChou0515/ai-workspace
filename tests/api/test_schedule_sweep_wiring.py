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


def test_the_app_wires_both_collaborators_into_the_starter() -> None:
    """The body lives in `start_page_schedule`, so this pins that `create_app`
    actually reaches it — with BOTH things it needs.

    What the body does is driven in `tests/api/test_page_schedule_start.py`;
    what a test cannot drive is whether the composition root still points at it.
    An extraction that nothing calls is the same as no extraction, and the
    symptom would be a scheduled run reading the user's own chat history.

    The orchestrator is read inside the closure on purpose — it is constructed
    later than this line — so passing it is a call-time act, not a captured one.
    """
    source = _APP.read_text(encoding="utf-8")

    closure = source.split("async def _start_page_schedule", 1)[-1].split("\n    lifespan", 1)[0]

    assert "start_page_schedule(" in closure, (
        "create_app no longer delegates to the extracted starter, so nothing "
        "that is driven by a test is what actually fires"
    )
    for arg in ("locator=locator", "orchestrator=workflow_orchestrator"):
        assert arg in closure, f"the starter is called without {arg}"


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
    body = source.split("async def start_page_schedule", 1)[-1].split("\ndef ", 1)[0]

    blocking = ("chat_for_schedule", "settle_run_chat", "slug_of", "profile_of")
    # Matched on the pair, not on one spelling: the formatter wraps a long call
    # so `to_thread(locator.x` and `to_thread(\n    locator.x` are the same thing,
    # and a guard that only knows one of them passes the day black reflows it.
    flat = " ".join(body.split())

    # COUNTED, not merely present. `settle_run_chat` has TWO call sites — the
    # post-start link and the failure cleanup — and an `in` check is satisfied by
    # either one. Reverting just the cleanup to a direct call left every test
    # green, which is the whole failure mode this file keeps producing: a guard
    # that passes because SOMETHING matched, not because the property holds.
    unoffloaded = []
    for name in blocking:
        calls = flat.count(f"locator.{name}")
        offloaded = flat.count(f"asyncio.to_thread(locator.{name}") + flat.count(
            f"asyncio.to_thread( locator.{name}"
        )
        if calls != offloaded:
            unoffloaded.append(f"{name} ({offloaded} of {calls} offloaded)")

    assert not unoffloaded, (
        f"{unoffloaded} — blocking specstar calls made directly on the event loop, "
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


def test_the_row_cap_knob_reaches_the_sweeper() -> None:
    """`server.max_page_schedules` has to arrive where it is enforced.

    The config ledger proves the setting is READ in `__main__`. Nothing proved
    the second hop: replacing `max_rows=...max_page_schedules` with the module
    default left 235 tests green, and `grep -rn max_page_schedules tests/` found
    nothing. A knob that is read and then dropped is worse than an absent one —
    an operator lowers it after an incident, watches the deploy go out, and the
    cap they set never applies.

    A source check because the failure is a WIRING choice: the sweeper is built
    once in a composition root, and both values are plausible integers, so
    nothing downstream can tell which one it got.
    """
    source = _APP.read_text(encoding="utf-8")

    call = source.split("UserScheduleSweeper(", 1)[-1].split("\n    )", 1)[0]
    flat = " ".join(call.split())

    assert "max_rows=" in flat, "the sweeper is built without a row cap at all"
    assert "max_page_schedules" in flat, (
        "the sweeper's row cap does not come from `server.max_page_schedules`, so "
        "the knob an operator sets is read and then dropped"
    )
