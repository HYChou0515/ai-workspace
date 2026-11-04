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
