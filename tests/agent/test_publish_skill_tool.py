"""`publish_skill(name)` — a workspace skill goes to the skill hub (plan P4).

Driven through the real tool entry, the way a turn calls it. The order inside
is the plan's: structure is checked first (a trap refuses, by name, before any
model is spent), then the registered-tool scan, then the AI review, then the
write — and the entry records where it came from (`source_item` / app /
profile) so the edit route (P8) can send its owner back to the item that made
it. `.origin` decides the lineage: a copy of someone ELSE's entry publishes as a
fork of it; a copy of one's own re-publishes the same entry; no `.origin`, or
one pointing at a package skill or an entry that no longer exists, is a root.
"""

from __future__ import annotations

import msgspec
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import publish_skill_impl
from workspace_app.api.skill_review import SkillReviewUnavailable
from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore, register_skill_hub
from workspace_app.apps.skill_payload import ORIGIN_FILE, SkillOrigin
from workspace_app.apps.skills import WORKSPACE_SKILL_DIR, install_hub_skill
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

OK = SkillHubReview(verdict="ok", notes=[], model="gpt-4o")


def _md(name: str = "triage-reflow", body: str = "1. Read the log with `read_file`.\n") -> bytes:
    return f"---\nname: {name}\ndescription: Triage reflow defects.\n---\n\n{body}".encode()


class _Reviewer:
    """The `review_skill_via` seam, scripted: answers a fixed review or raises,
    and remembers what it was asked to review."""

    def __init__(self, review: SkillHubReview = OK, raises: Exception | None = None) -> None:
        self.review = review
        self.raises = raises
        self.calls: list[tuple[str, dict[str, bytes], object]] = []

    async def __call__(self, parent_ctx, folder, payload, emit):  # noqa: ANN001, ANN202
        self.calls.append((folder, dict(payload), emit))
        if self.raises is not None:
            raise self.raises
        return self.review


def _hub() -> SkillHubStore:
    spec = make_spec(default_user="system")
    register_skill_hub(spec)
    return SkillHubStore(spec, MemoryFileStore())


def _ctx(
    hub: SkillHubStore | None,
    reviewer: _Reviewer | None,
    *,
    user: str = "alice",
    item: str = "inv-1",
) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id=item,
            files=WorkspaceFiles(MemoryFileStore()),
            app_slug="rca",
            template_profile="default",
            acting_user=user,
            skill_hub=hub,
            review_skill_via=reviewer,
            on_exec_output=lambda _b: None,
        )
    )


async def _put(
    ctx: RunContextWrapper[AgentToolContext], name: str, files: dict[str, bytes]
) -> None:
    ws, iid = ctx.context.files, ctx.context.investigation_id
    assert ws is not None and iid is not None
    for rel, data in files.items():
        await ws.write(iid, f"/{WORKSPACE_SKILL_DIR}/{name}/{rel}", data)


def _files(ctx: RunContextWrapper[AgentToolContext]) -> WorkspaceFiles:
    files = ctx.context.files
    assert files is not None
    return files


def _origin(entry: str) -> bytes:
    return msgspec.json.encode(SkillOrigin(source="hub", files={"SKILL.md": "x"}, entry=entry))


# ── the happy path, and what the entry records ───────────────────────────────


async def test_a_workspace_skill_publishes_with_its_files_and_where_it_came_from():
    hub, reviewer = _hub(), _Reviewer()
    ctx = _ctx(hub, reviewer)
    await _put(
        ctx,
        "triage-reflow",
        {"SKILL.md": _md(), "references/glossary.md": b"reflow: solder step\n"},
    )

    out = await publish_skill_impl(ctx, "triage-reflow")

    entry_id = hub.find("alice", "triage-reflow")
    assert entry_id is not None and entry_id in out, out
    assert "error" not in out
    entry = hub.get(entry_id)
    assert entry is not None
    assert (entry.owner, entry.name, entry.description) == (
        "alice",
        "triage-reflow",
        "Triage reflow defects.",
    )
    assert (entry.source_item, entry.source_app, entry.source_profile) == (
        "inv-1",
        "rca",
        "default",
    )
    assert entry.forked_from == ""
    assert entry.review == OK
    assert await hub.payload_of(entry_id) == {
        "SKILL.md": _md(),
        "references/glossary.md": b"reflow: solder step\n",
    }


async def test_the_body_is_scanned_against_the_real_tool_registry():
    """`exec` and `read_file` are registered tools; `deploy_rocket` is a word."""
    hub = _hub()
    ctx = _ctx(hub, _Reviewer())
    await _put(
        ctx, "s", {"SKILL.md": _md("s", "Run `exec`, then read_file it; never `deploy_rocket`.\n")}
    )

    out = await publish_skill_impl(ctx, "s")

    entry = hub.get(hub.find("alice", "s") or "")
    assert entry is not None and entry.referenced_tools == ["exec", "read_file"]
    assert "exec" in out and "read_file" in out


async def test_the_reviewer_sees_the_folder_and_relays_into_the_tool_card():
    reviewer = _Reviewer()
    ctx = _ctx(_hub(), reviewer)
    payload = {"SKILL.md": _md(), "scripts/x.py": b"print(1)\n"}
    await _put(ctx, "triage-reflow", payload)

    await publish_skill_impl(ctx, "triage-reflow")

    assert len(reviewer.calls) == 1
    folder, seen, emit = reviewer.calls[0]
    assert folder == "triage-reflow"
    assert seen == payload
    assert emit is ctx.context.on_exec_output, "the review's progress lands in this turn's card"


async def test_the_origin_manifest_is_not_part_of_what_ships():
    """`.origin` says where THIS copy came from. The hub writes its own for
    every entry; shipping the source's would tell an installer the wrong
    lineage."""
    hub = _hub()
    ctx = _ctx(hub, _Reviewer())
    await _put(ctx, "s", {"SKILL.md": _md("s"), ORIGIN_FILE: _origin("someone-elses")})

    await publish_skill_impl(ctx, "s")

    entry_id = hub.find("alice", "s")
    assert entry_id is not None
    assert ORIGIN_FILE not in await hub.payload_of(entry_id)


# ── the review is the gate, the notes are the reply ──────────────────────────


async def test_review_notes_are_in_the_reply_and_the_skill_still_publishes():
    """D4: judgement never blocks. The notes ride on the entry and on the reply
    the publisher reads in the chat."""
    notes = ["SKILL.md: the description never says when to use it", "scripts/x.py: hardcoded path"]
    hub = _hub()
    ctx = _ctx(hub, _Reviewer(SkillHubReview(verdict="notes", notes=notes, model="gpt-4o")))
    await _put(ctx, "s", {"SKILL.md": _md("s")})

    out = await publish_skill_impl(ctx, "s")

    assert "error" not in out
    for note in notes:
        assert note in out
    entry = hub.get(hub.find("alice", "s") or "")
    assert entry is not None and entry.review.notes == notes


async def test_an_unreachable_reviewer_fails_the_publish_and_writes_nothing():
    """Q9: no review, no entry. The reply says the review service is what
    failed, so the person retries later rather than editing a skill that was
    never read."""
    hub = _hub()
    ctx = _ctx(hub, _Reviewer(raises=SkillReviewUnavailable("giving up: APIConnectionError")))
    await _put(ctx, "s", {"SKILL.md": _md("s")})

    out = await publish_skill_impl(ctx, "s")

    assert out.startswith("error:") and "review" in out and "APIConnectionError" in out
    assert hub.find("alice", "s") is None


async def test_a_structural_trap_is_refused_by_name_before_any_review():
    """The three traps are why the hub validates at all: a name that does not
    match the folder installs as nothing, silently. Refused here, named, and
    NO model is spent on it — the review comes after structure."""
    hub, reviewer = _hub(), _Reviewer()
    ctx = _ctx(hub, reviewer)
    await _put(ctx, "triage-reflow", {"SKILL.md": _md("reflow-triage")})

    out = await publish_skill_impl(ctx, "triage-reflow")

    assert out.startswith("error:")
    assert "reflow-triage" in out and "triage-reflow" in out
    assert reviewer.calls == []
    assert hub.find("alice", "triage-reflow") is None


# ── lineage from .origin ─────────────────────────────────────────────────────


async def test_a_copy_of_someone_elses_entry_publishes_as_a_fork_of_it():
    hub = _hub()
    bobs = await hub.publish(
        owner="bob",
        name="triage-reflow",
        description="d",
        source_item="inv-bob",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md()},
        referenced_tools=[],
        review=OK,
    )
    ctx = _ctx(hub, _Reviewer())
    await _put(ctx, "triage-reflow", {"SKILL.md": _md(), ORIGIN_FILE: _origin(bobs)})

    out = await publish_skill_impl(ctx, "triage-reflow")

    mine = hub.find("alice", "triage-reflow")
    assert mine is not None and mine != bobs
    entry = hub.get(mine)
    assert entry is not None and entry.forked_from == bobs
    assert "fork" in out and "bob" in out


async def test_a_copy_of_ones_own_entry_republishes_the_same_entry():
    """Alice installed her own skill into a second item, changed it there, and
    publishes from there: that is a new revision of HER entry, not a fork."""
    hub = _hub()
    first = _ctx(hub, _Reviewer(), item="inv-1")
    await _put(first, "s", {"SKILL.md": _md("s", "v1\n")})
    await publish_skill_impl(first, "s")
    mine = hub.find("alice", "s")
    assert mine is not None

    second = _ctx(hub, _Reviewer(), item="inv-2")
    await _put(second, "s", {"SKILL.md": _md("s", "v2\n"), ORIGIN_FILE: _origin(mine)})
    out = await publish_skill_impl(second, "s")

    assert hub.find("alice", "s") == mine
    entry = hub.get(mine)
    assert entry is not None and entry.forked_from == ""
    assert entry.source_item == "inv-2", "the latest revision says where it was made"
    assert (await hub.payload_of(mine))["SKILL.md"] == _md("s", "v2\n")
    assert "updated" in out


async def test_ones_own_entry_copied_under_a_new_name_is_a_new_root_not_a_self_fork():
    """Forking is the NON-owner's path (Q4). Alice installing her own `s`,
    renaming the folder `s-v2` and publishing has made a second skill of her
    own — a root — not a fork of herself. (Same name would be a revision; that
    is the test above.)"""
    hub = _hub()
    first = _ctx(hub, _Reviewer(), item="inv-1")
    await _put(first, "s", {"SKILL.md": _md("s")})
    await publish_skill_impl(first, "s")
    mine = hub.find("alice", "s")
    assert mine is not None

    second = _ctx(hub, _Reviewer(), item="inv-2")
    await _put(second, "s-v2", {"SKILL.md": _md("s-v2"), ORIGIN_FILE: _origin(mine)})
    out = await publish_skill_impl(second, "s-v2")

    entry = hub.get(hub.find("alice", "s-v2") or "")
    assert entry is not None and entry.forked_from == ""
    assert "fork" not in out


async def test_a_copy_whose_upstream_is_gone_publishes_as_a_root():
    """Q10: an entry that cannot be found is gone. A fork of nothing is a root."""
    hub = _hub()
    ctx = _ctx(hub, _Reviewer())
    await _put(ctx, "s", {"SKILL.md": _md("s"), ORIGIN_FILE: _origin("no-such-entry")})

    await publish_skill_impl(ctx, "s")

    entry = hub.get(hub.find("alice", "s") or "")
    assert entry is not None and entry.forked_from == ""


async def test_after_publishing_the_folder_tracks_the_entry_it_just_became():
    """Review round 1: the publisher's own copy said "update available" right
    after they published from it — its `.origin` still described the version
    it was INSTALLED from, and Refresh then "kept" every file. Publishing
    rewrites the manifest to the version just published, so the folder tracks
    its own entry: no phantom update, and a later re-publish from another item
    IS one."""
    from workspace_app.apps.skills import skill_upstream

    hub = _hub()
    first = _ctx(hub, _Reviewer(), item="inv-1")
    await _put(first, "s", {"SKILL.md": _md("s", "v1\n")})
    await publish_skill_impl(first, "s")
    mine = hub.find("alice", "s")
    assert mine is not None
    second = _ctx(hub, _Reviewer(), item="inv-2")
    await install_hub_skill(_files(second), "inv-2", hub, mine)
    await _put(second, "s", {"SKILL.md": _md("s", "v2\n")})

    await publish_skill_impl(second, "s")

    up = await skill_upstream(
        _files(second), "inv-2", "rca", "default", "s", hub=hub, viewer="alice"
    )
    assert up is not None and (up.state, up.update_available) == ("live", False)
    # …and the first item, which still holds v1, now sees the update.
    up1 = await skill_upstream(
        _files(first), "inv-1", "rca", "default", "s", hub=hub, viewer="alice"
    )
    assert up1 is not None and up1.update_available is True


async def test_a_published_fork_tracks_the_fork_not_the_root():
    """Bob installed alice's entry and published his changes: his folder's
    `.origin` used to keep pointing at ALICE's entry, so her next re-publish
    showed on his row as an update to pull over his own work."""
    hub = _hub()
    alices = await hub.publish(
        owner="alice",
        name="triage-reflow",
        description="d",
        source_item="inv-alice",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md()},
        referenced_tools=[],
        review=OK,
    )
    bob = _ctx(hub, _Reviewer(), user="bob", item="inv-bob")
    await install_hub_skill(_files(bob), "inv-bob", hub, alices)
    await _put(bob, "triage-reflow", {"SKILL.md": _md(body="bob's take\n")})

    await publish_skill_impl(bob, "triage-reflow")

    bobs = hub.find("bob", "triage-reflow")
    assert bobs is not None and bobs != alices
    origin = msgspec.json.decode(
        await _files(bob).read("inv-bob", f"/{WORKSPACE_SKILL_DIR}/triage-reflow/{ORIGIN_FILE}"),
        type=SkillOrigin,
    )
    assert origin.entry == bobs


# ── refusals that name what to do ────────────────────────────────────────────


async def test_an_unknown_name_lists_the_skills_this_workspace_has():
    ctx = _ctx(_hub(), _Reviewer())
    await _put(ctx, "have-this", {"SKILL.md": _md("have-this")})

    out = await publish_skill_impl(ctx, "not-this")

    assert out.startswith("error:") and "not-this" in out and "have-this" in out


async def test_no_speaker_no_publish():
    """An entry has an owner. A turn with nobody behind it cannot make one."""
    ctx = _ctx(_hub(), _Reviewer(), user="")
    await _put(ctx, "s", {"SKILL.md": _md("s")})

    out = await publish_skill_impl(ctx, "s")

    assert out.startswith("error:")


async def test_without_the_seams_the_tool_says_where_it_works():
    ctx = _ctx(None, None)
    await _put(ctx, "s", {"SKILL.md": _md("s")})

    out = await publish_skill_impl(ctx, "s")

    assert out.startswith("error:") and "App workspace turn" in out


# ── the composition root wires both seams ────────────────────────────────────


async def test_create_app_wires_the_hub_and_a_reviewer_that_runs_on_the_apps_runner():
    """The two seams are deploy-owned (like `run_agent`), so the guarantee that
    a real turn HAS them lives in `create_app`, not in this tool. Built through
    the app's own turn builder: the reviewer that comes back drives the app's
    runner (the scripted one here), and the hub it comes back with is the one
    the app's routes will read (P6) — a store built anywhere else would publish
    into a hub nobody can list."""
    from workspace_app.api.app import create_app
    from workspace_app.api.events import MessageDelta, RunDone
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.resources.agent_config import AgentConfig
    from workspace_app.sandbox.mock import MockSandbox

    from ..api.conftest import register_rca_item

    spec = make_spec(default_user="alice")
    iid = register_rca_item(spec)
    runner = ScriptedAgentRunner([MessageDelta(text='{"notes": ["one note"]}'), RunDone()])
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)

    async def no_subagent(*_a: object, **_k: object) -> tuple[str, list[object]]:
        return "", []

    ctx = await app.state.chat_send._turn_ctx.build_chat_turn(
        iid,
        agent_config=AgentConfig(name="t", model="gpt-4o"),
        run_subagent=no_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="alice",
        speaker=None,
    )

    assert isinstance(ctx.skill_hub, SkillHubStore)
    assert ctx.review_skill_via is not None
    review = await ctx.review_skill_via(ctx, "s", {"SKILL.md": _md("s")}, None)
    assert review == SkillHubReview(verdict="notes", notes=["one note"], model="gpt-4o")
