"""P2 (#435) — ``create_entity`` reworked onto the non-idempotent shell: the dedup
identity is the capability's stable ``name`` (NOT the args-digest), so a gate revise
that changes the content re-runs the same site and MERGES into the entity it already
minted instead of double-creating. Journal-first self-dedup via a write-once
``created.json``; ``on_duplicate`` ∈ {update, skip}. All deterministic (no AI — that's P3).
"""

from __future__ import annotations

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.engine import StepFailed
from workspace_app.workflow.handle import WorkflowHandle


async def _wf() -> WorkflowHandle:
    store = MemoryFileStore()
    await store.write(
        "ws",
        "/.entity/issue/schema.yaml",
        b"path: issues\nfields:\n  title: {role: text}\n  status: {role: status}\n",
    )
    await store.write(
        "ws",
        "/.entity/issue/skeleton.md",
        b"---\ntitle: {{arg.title}}\nstatus: {{arg.status?}}\n---\n",
    )
    return WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm", user="alice")


async def test_two_named_sites_create_two_entities() -> None:
    """Two distinct capability sites (distinct ``name``) each mint their own entity —
    the identity is the site, not the content."""
    wf = await _wf()
    n1 = await wf.create_entity("issue", {"title": "A"}, name="file_a")
    n2 = await wf.create_entity("issue", {"title": "B"}, name="file_b")
    assert (n1, n2) == (1, 2)


async def test_same_site_revise_merges_instead_of_double_creating() -> None:
    """THE PRIMARY DRIVER. The same site re-run with CHANGED content (a gate revise)
    self-dedups: it merges into the entity it already minted (#1) rather than opening
    #2. The old args-digest key double-created here."""
    wf = await _wf()
    first = await wf.create_entity("issue", {"title": "Login bug"}, name="file_bug")
    assert first == 1

    # a revise: same site, new content
    again = await wf.create_entity("issue", {"title": "Login bug (clarified)"}, name="file_bug")
    assert again == 1  # SAME entity, not #2
    store = wf._store  # type: ignore[attr-defined]
    assert not await store.exists("ws", "/issues/2.md")  # no double-create
    body = (await store.read("ws", "/issues/1.md")).decode()
    assert "Login bug (clarified)" in body  # merge overlaid the declared field


async def test_on_duplicate_skip_returns_existing_untouched() -> None:
    """``on_duplicate='skip'`` returns the existing entity's number and does NOT touch
    it — ensure-exists without overwriting."""
    wf = await _wf()
    await wf.create_entity("issue", {"title": "A"}, name="site")
    n = await wf.create_entity("issue", {"title": "B"}, name="site", on_duplicate="skip")
    assert n == 1
    store = wf._store  # type: ignore[attr-defined]
    assert "title: A" in (await store.read("ws", "/issues/1.md")).decode()  # untouched


async def test_merge_preserves_human_edited_non_declared_fields() -> None:
    """保留人編輯: the merge patch is only the declared fields, so a field a human set
    that the workflow does NOT declare (status) survives the merge (决议3, no provenance
    needed — the declared-field set IS the boundary)."""
    wf = await _wf()
    await wf.create_entity("issue", {"title": "A"}, name="site")
    store = wf._store  # type: ignore[attr-defined]
    # a human sets status the workflow never declares
    await store.write("ws", "/issues/1.md", b"---\nstatus: in_progress\ntitle: A\n---\n")
    # workflow revises, declaring only title
    await wf.create_entity("issue", {"title": "A2"}, name="site")
    body = (await store.read("ws", "/issues/1.md")).decode()
    assert "title: A2" in body  # workflow's declared field updated
    assert "status: in_progress" in body  # human's non-declared field preserved


async def test_act_crash_after_create_does_not_double_create() -> None:
    """If act created the entity but its journal record was lost (crash before the
    write), a replay re-runs act — and the write-once ``created.json`` makes the create
    idempotent (reuse the already-minted number) rather than mint a second one."""
    wf = await _wf()
    n = await wf.create_entity("issue", {"title": "A"}, name="site")
    assert n == 1
    # act's published record is lost, but the create side effect + created.json landed
    await wf.delete("/.workflow/pm/step_site/main.json")
    again = await wf.create_entity("issue", {"title": "A"}, name="site")
    assert again == 1
    store = wf._store  # type: ignore[attr-defined]
    assert not await store.exists("ws", "/issues/2.md")


async def test_concurrent_store_conflict_on_merge_fails_the_step() -> None:
    """A merge that hits the store's optimistic lock (the entity changed under it,
    a cross-pod race) becomes a ``StepFailed`` — the element drops into ``failures[]``
    and the human's edit is not clobbered (§8), rather than a silent overwrite."""
    from workspace_app.entity import store as store_mod

    wf = await _wf()
    await wf.create_entity("issue", {"title": "A"}, name="site")

    orig_update = store_mod.EntityStore.update
    calls = {"n": 0}

    async def _conflicting_update(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise store_mod.EntityConflict("changed under me")

    store_mod.EntityStore.update = _conflicting_update  # type: ignore[method-assign]
    try:
        with pytest.raises(StepFailed):
            await wf.create_entity("issue", {"title": "B"}, name="site")
    finally:
        store_mod.EntityStore.update = orig_update  # type: ignore[method-assign]
    assert calls["n"] == 1


async def test_created_result_number_is_returned() -> None:
    """The handle returns the entity number for backward-compatible callers, sourced
    from the shell's published ``Result.fields['number']``."""
    wf = await _wf()
    assert await wf.create_entity("issue", {"title": "A"}, name="s") == 1
