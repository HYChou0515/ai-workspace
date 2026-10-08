"""What the skill hub's skill page reads beyond the detail
(docs/plan-skill-hub-ux-redo.md P2): version numbers on the timeline (D8),
file sizes and the script count for the files tab (D12), where the viewer
has the skill installed (D3), and what installing into each of an App's
workspaces would do (D6)."""

from __future__ import annotations

import msgspec

from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.apps.skills import SkillOrigin
from workspace_app.perm import Permission

from .conftest import Harness

VIEWER = "default-user"


def _md(body: str, name: str = "triage") -> bytes:
    return f"---\nname: {name}\ndescription: d\n---\n{body}\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _publish(
    hub: SkillHubStore,
    body: str,
    *,
    owner: str = "alice",
    name: str = "triage",
    extra: dict[str, bytes] | None = None,
    tools: list[str] | None = None,
) -> str:
    return await hub.publish(
        owner=owner,
        name=name,
        description=body,
        source_item="inv-src",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(body, name), **(extra or {})},
        referenced_tools=tools or [],
        review=SkillHubReview(verdict="ok"),
    )


def _revision(harness: Harness, entry_id: str) -> str:
    return harness.spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


def _item(harness: Harness, title: str, *verbs: str, owner: str = "bob") -> str:
    """An rca workspace `owner` made, shared with the viewer for `verbs`."""
    rm = harness.spec.get_resource_manager(RcaInvestigation)
    with rm.using(owner):
        return rm.create(
            RcaInvestigation(
                title=title,
                owner=owner,
                permission=Permission(
                    visibility="restricted",
                    read_meta=[f"user:{VIEWER}"],
                    **{v: [f"user:{VIEWER}"] for v in verbs},
                ),
            )
        ).resource_id


async def _copy(harness: Harness, item: str, name: str, entry: str) -> None:
    """A copy of `entry` sitting in `item`, as an install leaves it."""
    await harness.filestore.write(item, f"/.skill/{name}/SKILL.md", _md("x", name))
    origin = SkillOrigin(source="hub", files={}, entry=entry, commit="c")
    await harness.filestore.write(item, f"/.skill/{name}/.origin", msgspec.json.encode(origin))


# ── version numbers (D8) ─────────────────────────────────────────────────────


async def test_publishes_and_rollbacks_are_numbered_in_order_and_other_rows_are_not(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    first = _revision(harness, entry)
    await _publish(hub, "two")
    await hub.transfer(entry, VIEWER)
    master = hub.get(entry)
    assert master is not None
    await hub.rollback(entry, first, expected=master.commit)

    events = harness.client.get(f"/skill-hub/entries/{entry}/history").json()["events"]

    assert [(e["kind"], e["version"]) for e in events] == [
        ("rollback", 3),
        ("transfer", None),
        ("publish", 2),
        ("publish", 1),
    ]


async def test_a_viewer_who_does_not_see_permission_rows_gets_the_same_numbers(
    harness: Harness,
):
    """The owner's timeline has permission rows; another reader's does not.
    The numbers are the version's, so both say v2 for the same version."""
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    await hub.set_permission(entry, Permission(visibility="public"))
    await _publish(hub, "two")

    events = harness.client.get(f"/skill-hub/entries/{entry}/history").json()["events"]

    assert [(e["kind"], e["version"]) for e in events] == [("publish", 2), ("publish", 1)]


# ── files (D12) ──────────────────────────────────────────────────────────────


async def test_the_detail_and_a_version_list_each_file_with_its_size_and_count_scripts(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(
        hub,
        "one",
        extra={
            "references/g.md": b"12345",
            "scripts/run.py": b"print()\n",
            "scripts/lib/util.sh": b"echo\n",
            "references/scripts/x.md": b"not a script: not the top-level folder",
            "assets/chart.png": b"\x89PNG" + b"x" * 1000,
        },
    )

    detail = harness.client.get(f"/skill-hub/entries/{entry}").json()
    version = harness.client.get(
        f"/skill-hub/entries/{entry}/versions/{_revision(harness, entry)}"
    ).json()

    for body in (detail, version):
        sizes = {f["path"]: f["size"] for f in body["files"]}
        assert sizes["references/g.md"] == 5
        assert sizes["assets/chart.png"] == 1004, "an LFS file's content, not its pointer"
        assert sizes["SKILL.md"] == len(_md("one"))
        assert [f["path"] for f in body["files"]] == sorted(sizes)
        assert body["scripts"] == 2, "files under scripts/ — the skill convention"


async def test_the_detail_says_when_the_content_last_changed(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "one")

    detail = harness.client.get(f"/skill-hub/entries/{entry}").json()

    row = hub.get(entry)
    assert row is not None and row.content_at is not None
    assert detail["updated_at"] is not None
    assert detail["updated_at"].startswith(row.content_at.isoformat()[:19])


async def test_the_detail_names_its_current_revision_so_a_file_can_be_opened(harness: Harness):
    """The files tab opens a file through the version-file route, which is
    keyed by revision: the detail says which revision is current."""
    hub = _hub(harness)
    entry = await _publish(hub, "one", extra={"notes.md": b"hello"})

    detail = harness.client.get(f"/skill-hub/entries/{entry}").json()

    assert detail["revision"] == _revision(harness, entry)
    one = harness.client.get(
        f"/skill-hub/entries/{entry}/versions/{detail['revision']}/file",
        params={"path": "notes.md"},
    )
    assert one.json()["text"] == "hello"


# ── where the viewer has it (D3) ─────────────────────────────────────────────


async def test_installs_lists_the_viewers_editable_workspaces_holding_a_copy_of_this_entry(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    other = await _publish(hub, "x", owner="carol")  # same name, another entry
    installed = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    assert installed.status_code == 200, installed.text
    read_only = _item(harness, "theirs", "read_content")
    await _copy(harness, read_only, "triage", entry)
    holds_other = _item(harness, "other copy", "read_content", "edit_content")
    await _copy(harness, holds_other, "triage", other)
    renamed = _item(harness, "renamed", "read_content", "edit_content")
    await _copy(harness, renamed, "my-triage", entry)

    res = harness.client.get(f"/skill-hub/entries/{entry}/installs")

    assert res.status_code == 200, res.text
    assert res.json()["installs"] == [{"app": "rca", "item_id": harness.iid, "title": "t"}]


async def test_installs_of_an_entry_the_viewer_cannot_read_is_404(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    await hub.set_permission(entry, Permission(visibility="private"))

    assert harness.client.get(f"/skill-hub/entries/{entry}/installs").status_code == 404


# ── what installing would do (D6) ────────────────────────────────────────────


async def test_targets_lists_an_apps_editable_workspaces_with_what_installing_would_do(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(hub, "one", tools=["exec", "query_entity"])
    carols = await _publish(hub, "x", owner="carol")
    harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    free = _item(harness, "free", "read_content", "edit_content")
    taken = _item(harness, "taken", "read_content", "edit_content")
    await _copy(harness, taken, "triage", carols)
    hand_written = _item(harness, "hand", "read_content", "edit_content")
    await harness.filestore.write(hand_written, "/.skill/triage/SKILL.md", _md("mine"))
    read_only = _item(harness, "read only", "read_content")

    res = harness.client.get(f"/skill-hub/entries/{entry}/targets", params={"app": "rca"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["missing_tools"] == ["query_entity"]
    by_id = {t["item_id"]: t for t in body["items"]}
    assert read_only not in by_id, "only workspaces the viewer may edit"
    assert by_id[harness.iid]["state"] == "installed"
    assert by_id[free]["state"] == "ok"
    assert (by_id[taken]["state"], by_id[taken]["owner"]) == ("name_taken", "carol")
    assert (by_id[hand_written]["state"], by_id[hand_written]["owner"]) == ("name_taken", "")
    assert by_id[free]["title"] == "free"


async def test_targets_for_an_unknown_app_is_an_empty_list(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "one")

    res = harness.client.get(f"/skill-hub/entries/{entry}/targets", params={"app": "nope"})

    assert res.status_code == 200, res.text
    assert res.json() == {"missing_tools": [], "items": []}


# ── review round 1 ───────────────────────────────────────────────────────────


def _record_wakes(harness: Harness, monkeypatch) -> list[bool]:  # noqa: ANN001
    """Every `wake` the routes pass the workspace files facade."""
    files = harness.spa_client.app.state.workspace_files  # ty: ignore[unresolved-attribute]
    seen: list[bool] = []
    real_read, real_ls = files.read, files.ls

    async def read(workspace_id: str, path: str, *, wake: bool = True) -> bytes:
        seen.append(wake)
        return await real_read(workspace_id, path, wake=wake)

    async def ls(workspace_id: str, prefix: str = "", *, wake: bool = True) -> list[str]:
        seen.append(wake)
        return await real_ls(workspace_id, prefix, wake=wake)

    monkeypatch.setattr(files, "read", read)
    monkeypatch.setattr(files, "ls", ls)
    return seen


async def test_installs_and_targets_never_wake_a_sandbox(harness: Harness, monkeypatch):
    """They read every workspace the viewer can edit, on every skill page
    open: waking one rebuilds a reaped sandbox, so N idle workspaces cost N
    rebuilds a view. A gone sandbox has nothing newer than its durable copy."""
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    other = await _publish(hub, "x", owner="carol")
    taken = _item(harness, "taken", "read_content", "edit_content")
    await _copy(harness, taken, "triage", other)
    seen = _record_wakes(harness, monkeypatch)

    assert harness.client.get(f"/skill-hub/entries/{entry}/installs").status_code == 200
    assert harness.client.get(f"/skill-hub/entries/{entry}/targets?app=rca").status_code == 200

    assert seen and not any(seen), seen


async def test_a_copy_with_an_unreadable_record_does_not_break_the_page(harness: Harness):
    """Someone else's hand-edited `.origin` in a workspace shared with the
    viewer: that workspace is left out of where-installed and offered as
    taken (a folder of that name is there), and the rest still answer."""
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    broken = _item(harness, "broken", "read_content", "edit_content")
    await harness.filestore.write(broken, "/.skill/triage/SKILL.md", _md("x"))
    await harness.filestore.write(broken, "/.skill/triage/.origin", b"{not json")
    harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})

    installs = harness.client.get(f"/skill-hub/entries/{entry}/installs")
    targets = harness.client.get(f"/skill-hub/entries/{entry}/targets?app=rca")

    assert installs.status_code == 200, installs.text
    assert [i["item_id"] for i in installs.json()["installs"]] == [harness.iid]
    assert targets.status_code == 200, targets.text
    by_id = {t["item_id"]: t for t in targets.json()["items"]}
    assert by_id[broken]["state"] == "name_taken"


async def test_a_fork_from_a_version_is_the_viewers_own_not_an_install(harness: Harness):
    """「從這一版 fork」 copies a version as a starting point of the viewer's
    own (`.origin.forked`): not 「已經裝了」, but a folder of that name."""
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    started = _item(harness, "started", "read_content", "edit_content")
    await harness.filestore.write(started, "/.skill/triage/SKILL.md", _md("x"))
    origin = SkillOrigin(source="hub", files={}, entry=entry, commit="c", forked=True)
    await harness.filestore.write(started, "/.skill/triage/.origin", msgspec.json.encode(origin))

    installs = harness.client.get(f"/skill-hub/entries/{entry}/installs").json()["installs"]
    targets = harness.client.get(f"/skill-hub/entries/{entry}/targets?app=rca").json()["items"]

    assert started not in [i["item_id"] for i in installs]
    by_id = {t["item_id"]: t for t in targets}
    # The viewer's own folder: no one else's name on it (round 2).
    assert (by_id[started]["state"], by_id[started]["owner"]) == ("name_taken", "")


async def test_a_folder_holding_only_the_record_is_not_an_install(harness: Harness):
    """The install route accepts it (nothing of the skill is there), so the
    dialog offers it and the sidebar does not list it."""
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    hollow = _item(harness, "hollow", "read_content", "edit_content")
    origin = SkillOrigin(source="hub", files={}, entry=entry, commit="c")
    await harness.filestore.write(hollow, "/.skill/triage/.origin", msgspec.json.encode(origin))

    installs = harness.client.get(f"/skill-hub/entries/{entry}/installs").json()["installs"]
    targets = harness.client.get(f"/skill-hub/entries/{entry}/targets?app=rca").json()["items"]

    assert hollow not in [i["item_id"] for i in installs]
    assert {t["item_id"]: t["state"] for t in targets}[hollow] == "ok"
    res = harness.client.post(f"/a/rca/items/{hollow}/skills/install", json={"entry_id": entry})
    assert res.status_code == 200, res.text


async def test_the_forks_on_an_originals_page_name_it_and_count_their_own(harness: Harness):
    hub = _hub(harness)
    root = await _publish(hub, "one")
    fork = await _publish(hub, "f", owner="bob")
    rm = harness.spec.get_resource_manager(SkillHubEntry)
    rm.update(fork, msgspec.structs.replace(rm.get(fork).data, forked_from=root))
    grand = await _publish(hub, "g", owner="carol")
    rm.update(grand, msgspec.structs.replace(rm.get(grand).data, forked_from=fork))

    forks = harness.client.get(f"/skill-hub/entries/{root}").json()["forks"]

    assert [f["id"] for f in forks] == [fork]
    assert forks[0]["origin"] == {"owner": "alice", "name": "triage"}
    assert forks[0]["fork_count"] == 1


async def test_a_busy_workspace_is_said_not_failed(harness: Harness, monkeypatch):
    from workspace_app.sandbox.protocol import SandboxBusy

    hub = _hub(harness)
    entry = await _publish(hub, "one")
    busy = _item(harness, "busy", "read_content", "edit_content")
    files = harness.spa_client.app.state.workspace_files  # ty: ignore[unresolved-attribute]
    real_read = files.read

    async def read(workspace_id: str, path: str, *, wake: bool = True) -> bytes:
        if workspace_id == busy:
            raise SandboxBusy(workspace_id)
        return await real_read(workspace_id, path, wake=wake)

    monkeypatch.setattr(files, "read", read)

    installs = harness.client.get(f"/skill-hub/entries/{entry}/installs")
    targets = harness.client.get(f"/skill-hub/entries/{entry}/targets?app=rca")

    assert installs.status_code == 200 and targets.status_code == 200
    assert {t["item_id"]: t["state"] for t in targets.json()["items"]}[busy] == "unavailable"
