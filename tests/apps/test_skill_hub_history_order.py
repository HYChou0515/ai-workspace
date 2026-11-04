"""The skill hub history numbers versions in the order they were WRITTEN, on
every backend.

Found recording the release-notes demo on the disk backend (#1004): the newest
publish read "v1", the first read "v2 · current", and its note sat under the
wrong number. `history()` walked `list_revisions()` as if it were oldest first;
the in-memory backend every other test uses happens to answer that way, the
disk one does not. `updates_since` reads the same order, so the item reminder
named the wrong version too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from specstar import BackendBinding, BackendConfig, ConnectionProfile, SpecStar

from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore, register_skill_hub
from workspace_app.apps.skill_hub_git import SkillHubRepos
from workspace_app.resources import make_spec

V1 = {"SKILL.md": b"---\nname: triage\ndescription: one\n---\nline\n"}
V2 = {"SKILL.md": b"---\nname: triage\ndescription: two\n---\nline\nmore\n"}
V3 = {"SKILL.md": b"---\nname: triage\ndescription: three\n---\nline\nmore\nagain\n"}


def _disk(root: Path) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


@pytest.fixture(params=["memory", "disk"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> SkillHubStore:
    backend = _disk(tmp_path / "store") if request.param == "disk" else None
    spec: SpecStar = make_spec(default_user="alice", backend=backend)
    register_skill_hub(spec)
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))


async def _publish(store: SkillHubStore, payload: dict[str, bytes], note: str = "") -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=payload,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok", notes=[]),
        release_note=note,
    )


async def test_versions_are_numbered_oldest_first_and_listed_newest_first(
    store: SkillHubStore,
) -> None:
    entry = await _publish(store, V1)
    await _publish(store, V2, "second")
    await _publish(store, V3, "third")
    row = store.get(entry)
    assert row is not None

    history = [e for e in await store.history(entry, viewer="alice") if e.version]
    assert [(e.version, e.release_note) for e in history] == [(3, "third"), (2, "second"), (1, "")]
    assert [e.at for e in history] == sorted((e.at for e in history), reverse=True)
    assert history[0].current and history[0].commit == row.commit
    assert not any(e.current for e in history[1:])


async def test_what_a_copy_missed_is_the_versions_after_its_own(store: SkillHubStore) -> None:
    entry = await _publish(store, V1)
    installed = store.get(entry)
    assert installed is not None
    await _publish(store, V2, "second")
    await _publish(store, V3, "third")

    missed = await store.updates_since(entry, installed.commit, viewer="alice")
    assert [(e.version, e.release_note) for e in missed] == [(3, "third"), (2, "second")]
