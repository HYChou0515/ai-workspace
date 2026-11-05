"""`publish_skill` and release notes (docs/plan-skill-hub-release-notes.md P2).

From the second version of an entry on, a publish needs a `release_note`:
without one nothing is published — no review is spent — and the tool answers
with what changed since the current version, for the AI to draft the note
from and confirm with the author. A first version (a new entry, or a new
fork) needs none.
"""

from __future__ import annotations

from workspace_app.agent.tools import publish_skill_impl

from .test_publish_skill_tool import _ctx, _hub, _md, _put, _Reviewer


async def _first(hub, item: str = "inv-1"):  # noqa: ANN001, ANN202
    ctx = _ctx(hub, _Reviewer(), item=item)
    await _put(ctx, "s", {"SKILL.md": _md("s", "1. Read the log.\n"), "old.md": b"gone\n"})
    assert "published" in await publish_skill_impl(ctx, "s")
    return ctx


async def test_a_second_version_without_a_note_publishes_nothing_and_says_what_changed():
    hub = _hub()
    ctx = await _first(hub)
    entry = hub.find("alice", "s")
    assert entry is not None
    before = hub.get(entry)
    assert before is not None
    await _put(
        ctx, "s", {"SKILL.md": _md("s", "1. Read the log.\n2. Check the oven.\n"), "new.md": b"n\n"}
    )
    await ctx.context.files.delete(ctx.context.investigation_id, "/.skill/s/old.md")
    reviewer = _Reviewer()
    ctx.context.review_skill_via = reviewer

    out = await publish_skill_impl(ctx, "s")

    after = hub.get(entry)
    assert after is not None and after.commit == before.commit, "nothing was published"
    assert reviewer.calls == [], "no review is spent on a publish that cannot happen yet"
    assert "release_note" in out
    assert "+2. Check the oven." in out  # the change, to draft from
    assert "new.md" in out and "added" in out
    assert "old.md" in out and "removed" in out


async def test_a_second_version_with_a_note_publishes_with_it():
    hub = _hub()
    ctx = await _first(hub)
    entry = hub.find("alice", "s")
    assert entry is not None
    await _put(ctx, "s", {"SKILL.md": _md("s", "1. Read the log.\n2. Check the oven.\n")})

    out = await publish_skill_impl(ctx, "s", release_note="Adds the oven check.")

    assert "published" in out
    events = await hub.history(entry, viewer="alice")
    assert events[0].version == 2 and events[0].release_note == "Adds the oven check."


async def test_a_blank_note_is_no_note():
    hub = _hub()
    ctx = await _first(hub)
    entry = hub.find("alice", "s")
    assert entry is not None
    before = hub.get(entry)
    assert before is not None
    await _put(ctx, "s", {"SKILL.md": _md("s", "changed\n")})

    out = await publish_skill_impl(ctx, "s", release_note="   \n ")

    after = hub.get(entry)
    assert after is not None and after.commit == before.commit
    assert "release_note" in out


async def test_a_first_version_needs_no_note():
    hub = _hub()
    ctx = _ctx(hub, _Reviewer())
    await _put(ctx, "s", {"SKILL.md": _md("s", "v1\n")})

    out = await publish_skill_impl(ctx, "s")

    assert "published" in out
    assert hub.find("alice", "s") is not None


def test_the_change_shows_an_added_text_file_and_bounds_what_it_diffs():
    """Review round 1 (#1004): an added `references/` file came back as its name
    alone, so the note could only say "added a file"; and a large text file
    was diffed in full — difflib on the event loop, 7.7 s for 18.8 MiB — before
    the 6000-character cap cut the result. Added text is shown as added lines;
    a file over the hub's own `DIFF_TEXT_CAP` (either side) gets its status line
    only, as the hub's diff does."""
    import time

    from workspace_app.agent.tools import _changes_since
    from workspace_app.apps.skill_hub import DIFF_TEXT_CAP

    shown = _changes_since(
        {"SKILL.md": b"a\n"}, {"SKILL.md": b"a\n", "references/t.md": b"A spike is 3x.\n"}
    )
    assert "references/t.md: added" in shown
    assert "+A spike is 3x." in shown

    big_old = b"".join(b"line %d\n" % i for i in range(DIFF_TEXT_CAP // 6))
    big_new = big_old.replace(b"line 7\n", b"line seven\n")
    assert len(big_old) > DIFF_TEXT_CAP
    start = time.perf_counter()
    big = _changes_since({"data.csv": big_old}, {"data.csv": big_new})
    assert time.perf_counter() - start < 0.5
    assert big.startswith("data.csv: changed")
    assert "line seven" not in big
    assert "too large" in big


def test_the_model_is_offered_a_way_to_send_no_note():
    """Review round 1 (#1004): the tool is built strict, which lists every
    argument as required; a `str` note had no "not setting it" spelling, so a
    model had to invent one on every call — a republish's note included,
    skipping the ask. Nullable, it gets the platform's absent convention
    (`null`), stated on the tool."""
    from agents import function_tool

    tool = function_tool(publish_skill_impl, strict_mode=True)
    prop = tool.params_json_schema["properties"]["release_note"]
    assert {"type": "null"} in prop.get("anyOf", []), prop


async def test_a_null_note_is_no_note():
    hub = _hub()
    ctx = await _first(hub)
    await _put(ctx, "s", {"SKILL.md": _md("s", "v2\n")})
    out = await publish_skill_impl(ctx, "s", None)
    assert out.startswith("not published yet")


async def test_a_note_too_long_to_read_is_sent_back_to_be_shortened():
    """Review round 1: a note is stored verbatim and returned in every history
    read and every behind copy's skill listing, so a pasted 6000-character diff
    would sit there for good."""
    from workspace_app.agent.tools import RELEASE_NOTE_MAX

    hub = _hub()
    ctx = await _first(hub)
    entry = hub.find("alice", "s")
    assert entry is not None
    before = hub.get(entry)
    await _put(ctx, "s", {"SKILL.md": _md("s", "v2\n")})
    out = await publish_skill_impl(ctx, "s", "x" * (RELEASE_NOTE_MAX + 1))
    assert out.startswith("error:") and str(RELEASE_NOTE_MAX) in out
    after = hub.get(entry)
    assert before is not None and after is not None and after.commit == before.commit
    assert "published" in await publish_skill_impl(ctx, "s", "x" * RELEASE_NOTE_MAX)
    # The model is told the same number the tool enforces.
    assert f"at most {RELEASE_NOTE_MAX} characters" in (publish_skill_impl.__doc__ or "")


async def test_the_change_is_computed_off_the_event_loop(monkeypatch):
    """The same review: the diff ran inside the async tool. It goes through
    `asyncio.to_thread`, so a slow one stalls this turn and not the pod."""
    import asyncio

    import workspace_app.agent.tools as tools

    hub = _hub()
    ctx = await _first(hub)
    await _put(ctx, "s", {"SKILL.md": _md("s", "v2\n")})
    seen: list[object] = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        seen.append(fn)
        return await real(fn, *a, **k)

    monkeypatch.setattr(tools.asyncio, "to_thread", spy)
    out = await publish_skill_impl(ctx, "s")
    assert out.startswith("not published yet")
    assert tools._changes_since in seen
