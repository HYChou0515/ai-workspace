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
import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import publish_skill_impl
from workspace_app.api.skill_review import SkillReviewUnavailable
from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore, register_skill_hub
from workspace_app.apps.skill_payload import ORIGIN_FILE, SkillOrigin
from workspace_app.apps.skills import WORKSPACE_SKILL_DIR, install_hub_skill
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
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


async def test_a_copy_of_an_entry_the_publisher_may_no_longer_read_publishes_as_a_root():
    """Review round 1: the fork decision read the upstream with `hub.get`, so
    a copy of an entry its owner had taken PRIVATE still published as a fork —
    and the reply named that owner. Q10 again: unreadable is gone, and gone
    publishes as a root."""
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
    hub.set_permission(bobs, Permission(visibility="private"))
    ctx = _ctx(hub, _Reviewer())
    await _put(ctx, "triage-reflow", {"SKILL.md": _md(), ORIGIN_FILE: _origin(bobs)})

    out = await publish_skill_impl(ctx, "triage-reflow")

    entry = hub.get(hub.find("alice", "triage-reflow") or "")
    assert entry is not None and entry.forked_from == ""
    assert "bob" not in out and "fork" not in out


async def test_the_reply_says_the_visibility_the_entry_actually_has():
    """Review round 1: "It is public by default." was unconditional, so a
    re-publish of an entry its owner had UNPUBLISHED told the agent — and the
    user — it was public. The sentence is read back from the row."""
    hub = _hub()
    ctx = _ctx(hub, _Reviewer())
    await _put(ctx, "s", {"SKILL.md": _md("s")})
    first = await publish_skill_impl(ctx, "s")
    assert "public" in first
    mine = hub.find("alice", "s")
    assert mine is not None
    hub.set_permission(mine, Permission(visibility="private"))

    again = await publish_skill_impl(ctx, "s")

    assert "public by default" not in again
    assert "unpublished" in again or "private" in again


async def test_someone_elses_skill_under_a_name_i_already_publish_is_refused_not_merged():
    """Veracity lens, round 1: bob publishes `triage`; he installs alice's
    `triage` into another item and publishes from there. Identity (`owner/name`)
    said "revision of bob's", `.origin` said "fork of alice's", and the code
    took the first while silently dropping the second — bob's root was
    overwritten with alice's content and no lineage recorded. Two rules that
    both apply is a refusal that names them, not a coin toss."""
    hub = _hub()
    bob = _ctx(hub, _Reviewer(), user="bob", item="inv-bob-1")
    await _put(bob, "triage", {"SKILL.md": _md("triage", "bob's own\n")})
    await publish_skill_impl(bob, "triage")
    bobs = hub.find("bob", "triage")
    assert bobs is not None
    alices = await hub.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="inv-alice",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md("triage", "alice's\n")},
        referenced_tools=[],
        review=OK,
    )
    other = _ctx(hub, _Reviewer(), user="bob", item="inv-bob-2")
    await install_hub_skill(_files(other), "inv-bob-2", hub, alices)

    out = await publish_skill_impl(other, "triage")

    assert out.startswith("error:") and "alice" in out and "triage" in out
    assert (await hub.payload_of(bobs))["SKILL.md"] == _md("triage", "bob's own\n")
    assert hub.forks_of(alices) == []


async def test_republishing_ones_own_fork_from_a_fresh_copy_of_the_root_is_a_revision_of_the_fork():
    """The F6 refusal's one exception, pinned (review round 2 found it
    unpinned): bob already publishes `triage` AS A FORK of alice's; he
    installs alice's root again elsewhere and publishes — that is a new
    revision of his fork, lineage kept, not a clash."""
    hub = _hub()
    alices = await hub.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="inv-alice",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md("triage", "alice v1\n")},
        referenced_tools=[],
        review=OK,
    )
    first = _ctx(hub, _Reviewer(), user="bob", item="inv-bob-1")
    await install_hub_skill(_files(first), "inv-bob-1", hub, alices)
    await _put(first, "triage", {"SKILL.md": _md("triage", "bob's take\n")})
    await publish_skill_impl(first, "triage")
    bobs = hub.find("bob", "triage")
    assert bobs is not None
    fork = hub.get(bobs)
    assert fork is not None and fork.forked_from == alices
    second = _ctx(hub, _Reviewer(), user="bob", item="inv-bob-2")
    await install_hub_skill(_files(second), "inv-bob-2", hub, alices)
    await _put(second, "triage", {"SKILL.md": _md("triage", "bob's take v2\n")})

    out = await publish_skill_impl(second, "triage")

    assert "error" not in out and "updated your earlier version" in out
    assert hub.find("bob", "triage") == bobs
    entry = hub.get(bobs)
    assert entry is not None and entry.forked_from == alices
    assert (await hub.payload_of(bobs))["SKILL.md"] == _md("triage", "bob's take v2\n")


async def test_a_publish_that_cannot_write_its_manifest_is_refused_before_anything_is_published():
    """Review round 2 (regression lens): the `.origin` rewrite came AFTER the
    hub write, so on a workspace with less room than that manifest the entry
    went live for everyone while the reply said "workspace full". The room is
    checked first — and the reviewer is not spent on a publish that cannot
    finish."""
    from workspace_app.files.facade import WorkspaceFull

    hub, reviewer = _hub(), _Reviewer()
    md = _md("s")
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=WorkspaceFiles(MemoryFileStore(), quota=len(md)),
            app_slug="rca",
            template_profile="default",
            acting_user="alice",
            skill_hub=hub,
            review_skill_via=reviewer,
            on_exec_output=lambda _b: None,
        )
    )
    await _put(ctx, "s", {"SKILL.md": md})  # fills the workspace exactly

    with pytest.raises(WorkspaceFull):
        await publish_skill_impl(ctx, "s")

    assert hub.find("alice", "s") is None
    assert reviewer.calls == []


async def test_when_the_workspace_fills_during_the_review_the_reply_says_what_was_published():
    """The residue the up-front check cannot close: the room is there when the
    publish starts and gone by the time the manifest is written (something
    else wrote meanwhile — here, the reviewer double does). The entry IS live
    at that point, so the reply says so and names what could not be written,
    instead of "workspace full" over a publish that happened."""
    hub = _hub()
    md = _md("s")
    files = WorkspaceFiles(MemoryFileStore(), quota=len(md) + 400)

    class _FillsTheWorkspace(_Reviewer):
        async def __call__(self, parent_ctx, folder, payload, emit):  # noqa: ANN001, ANN202
            await files.write("inv-1", "/big.bin", b"x" * 400)
            return await super().__call__(parent_ctx, folder, payload, emit)

    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=files,
            app_slug="rca",
            template_profile="default",
            acting_user="alice",
            skill_hub=hub,
            review_skill_via=_FillsTheWorkspace(),
            on_exec_output=lambda _b: None,
        )
    )
    await _put(ctx, "s", {"SKILL.md": md})

    out = await publish_skill_impl(ctx, "s")

    entry = hub.find("alice", "s")
    assert entry is not None
    assert "published skill 's'" in out and entry in out
    assert ".origin" in out and "full" in out
    assert not await files.exists("inv-1", "/.skill/s/.origin")


async def test_a_folder_over_the_cap_is_refused_from_its_sizes_without_reading_it():
    """Review round 2: the 20 MiB cap was checked after the whole folder was
    already in memory. The sizes are cheap metadata (`stat_all`); the bytes
    are read only for a folder that fits."""
    from workspace_app.apps.skill_hub import SKILL_HUB_MAX_BYTES

    class _CountsReads(MemoryFileStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        async def read(self, workspace_id: str, path: str) -> bytes:
            self.reads += 1
            return await super().read(workspace_id, path)

    hub = _hub()
    store = _CountsReads()
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=WorkspaceFiles(store),
            app_slug="rca",
            template_profile="default",
            acting_user="alice",
            skill_hub=hub,
            review_skill_via=_Reviewer(),
            on_exec_output=lambda _b: None,
        )
    )
    huge = {"SKILL.md": _md("s"), "assets/huge.bin": b"x" * (SKILL_HUB_MAX_BYTES + 1)}
    await _put(ctx, "s", huge)
    store.reads = 0

    out = await publish_skill_impl(ctx, "s")

    assert out.startswith("error:") and "MiB" in out
    assert store.reads == 0


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
