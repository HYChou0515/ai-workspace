"""The WorkflowHandle file/IO surface (manual §3) — json round-trip + glob."""

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle


async def test_sub_handle_gives_each_element_its_own_turn_lane() -> None:
    """`wf.sub_handle(k)` yields a child that SHARES the workspace + journal but drives
    its agent turns on a DISTINCT turn lane, so N map elements' turns don't serialize
    behind one ChatTurnEngine key (#429 P5). Without a wired factory it degrades to the
    parent's lane (serialized) — safe default."""
    store = MemoryFileStore()
    wf = WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm")

    # no factory wired → the child shares the parent's drive_turn (graceful degrade)
    async def parent_turn(prompt, tools):
        return "p"

    wf.drive_turn = parent_turn
    assert wf.sub_handle("a").drive_turn is parent_turn

    # a wired factory binds a distinct lane per subkey
    seen: list[str] = []

    def factory(subkey: str):
        async def drive(prompt, tools):
            seen.append(subkey)
            return "ok"

        return drive

    wf.sub_turn = factory
    a, b = wf.sub_handle("a"), wf.sub_handle("b")
    assert a.drive_turn is not b.drive_turn  # distinct lanes → concurrent
    assert a.journal_dir == wf.journal_dir  # shared journal/workspace
    await a.write("/shared.txt", "x")
    assert await wf.read_text("/shared.txt") == "x"  # writes land in the same workspace
    await a.drive_turn("p", None)
    await b.drive_turn("p", None)
    assert seen == ["a", "b"]


async def test_read_write_json_and_text_round_trip(wf: WorkflowHandle):
    await wf.write_json("plan/f.json", {"collection": "a"})
    assert await wf.read_json("plan/f.json") == {"collection": "a"}
    await wf.write("/notes.md", "hello")
    assert await wf.read_text("notes.md") == "hello"
    assert await wf.exists("notes.md")
    await wf.delete("notes.md")
    assert not await wf.exists("notes.md")


async def test_glob_matches_patterns_minus_exclude_sorted(wf: WorkflowHandle):
    for p in ("inputs/b.txt", "inputs/a.txt", "inputs/input.json", "other/c.txt"):
        await wf.write(p, "x")
    # the canonical intake spec: everything under inputs/ except input.json
    files = await wf.glob(["inputs/*"], exclude=["inputs/input.json"])
    assert files == ["/inputs/a.txt", "/inputs/b.txt"]  # sorted, deterministic


async def test_glob_accepts_a_single_pattern_string(wf: WorkflowHandle):
    await wf.write("data/x.csv", "1")
    await wf.write("data/y.csv", "2")
    await wf.write("readme.md", "z")
    assert await wf.glob("data/*.csv") == ["/data/x.csv", "/data/y.csv"]


async def test_upload_dir_defaults_to_uploads(wf: WorkflowHandle):
    """#198: the run-scoped staging folder a workflow globs — fed from the profile's
    ``upload_dir`` so the glob default and the chat attach landing stay in sync.
    Omitted ⇒ ``uploads``."""
    assert wf.upload_dir == "uploads"


async def test_upload_dir_is_injectable():
    """The orchestrator injects the active profile's ``upload_dir`` so a workflow
    globs ``{upload_dir}/*`` without hardcoding the folder (the old #234 string)."""
    from workspace_app.filestore.memory import MemoryFileStore

    h = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", upload_dir="docs")
    assert h.upload_dir == "docs"


async def test_create_entity_uses_framework_numbering_and_is_idempotent() -> None:
    """`wf.create_entity` mints entities through the SAME EntityStore path as the
    UI/agent (framework numbering, no raw wf.write), and is journaled so a re-run
    with the same args never duplicates (#419 §C, workflow接軌)."""
    import pytest

    from workspace_app.entity.store import EntityStore  # noqa: F401  (import parity)
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.workflow.engine import StepFailed

    store = MemoryFileStore()
    await store.write(
        "ws", "/.entity/issue/schema.yaml", b"path: issues\nfields:\n  title: {role: text}\n"
    )
    await store.write("ws", "/.entity/issue/skeleton.md", b"---\ntitle: {{arg.title}}\n---\n")
    wf = WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm", user="alice")

    n1 = await wf.create_entity("issue", {"title": "A"})
    n2 = await wf.create_entity("issue", {"title": "B"})
    assert (n1, n2) == (1, 2)
    assert "title: A" in (await store.read("ws", "/issues/1.md")).decode()

    # re-run with the same args → journaled skip, no duplicate file
    again = await wf.create_entity("issue", {"title": "A"})
    assert again == 1
    assert not await store.exists("ws", "/issues/3.md")

    # unknown type fails loudly (before journaling)
    with pytest.raises(StepFailed):
        await wf.create_entity("nope", {"title": "X"})


async def test_update_entity_merges_patch_and_is_idempotent() -> None:
    """`wf.update_entity` mutates an entity through the SAME EntityStore path the UI/agent
    use (merge-patch, other fields preserved), and is journaled so re-running with the same
    patch is a skip — it never re-applies (#429 P2)."""
    from workspace_app.filestore.memory import MemoryFileStore

    store = MemoryFileStore()
    await store.write(
        "ws",
        "/.entity/issue/schema.yaml",
        b"path: issues\nfields:\n  title: {role: text}\n"
        b"  status: {role: status, values: [open, done]}\n",
    )
    await store.write(
        "ws",
        "/.entity/issue/skeleton.md",
        b"---\ntitle: {{arg.title}}\nstatus: {{arg.status}}\n---\n",
    )
    wf = WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm", user="alice")

    n = await wf.create_entity("issue", {"title": "A", "status": "open"})
    await wf.update_entity("issue", n, {"status": "done"})
    body = (await store.read("ws", "/issues/1.md")).decode()
    assert "status: done" in body and "title: A" in body  # patch merged, title preserved

    # tamper the file, then re-run the SAME patch: a journaled skip must NOT re-apply
    await store.write("ws", "/issues/1.md", b"---\ntitle: A\nstatus: open\n---\n")
    await wf.update_entity("issue", n, {"status": "done"})
    assert "status: open" in (await store.read("ws", "/issues/1.md")).decode()  # skipped


async def test_update_entity_retries_on_parallel_conflict(monkeypatch) -> None:
    """A parallel run that moves the record makes `EntityStore.update` raise
    `EntityConflict`; `wf.update_entity` re-reads and retries, so the update lands instead
    of lost-updating (#429 P2, gap 5). Exhausting the retries fails loud."""
    import pytest

    from workspace_app.entity import store as store_mod
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.workflow.engine import StepFailed

    store = MemoryFileStore()
    await store.write("ws", "/.entity/t/schema.yaml", b"path: ts\nfields:\n  s: {role: text}\n")
    await store.write("ws", "/.entity/t/skeleton.md", b"---\ns: {{arg.s}}\n---\n")
    wf = WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm", user="alice")
    n = await wf.create_entity("t", {"s": "x"})

    real_update = store_mod.EntityStore.update
    calls = {"n": 0}

    async def flaky(self, type_name, number, patch, *, expected_version=None):
        calls["n"] += 1
        if calls["n"] == 1:  # first attempt loses the optimistic race
            raise store_mod.EntityConflict("moved")
        return await real_update(self, type_name, number, patch, expected_version=expected_version)

    monkeypatch.setattr(store_mod.EntityStore, "update", flaky)
    await wf.update_entity("t", n, {"s": "y"}, phase="p")
    assert calls["n"] == 2  # retried once, then landed
    assert "s: y" in (await store.read("ws", "/ts/1.md")).decode()

    async def always_conflict(self, *a, **k):
        raise store_mod.EntityConflict("still moving")

    monkeypatch.setattr(store_mod.EntityStore, "update", always_conflict)
    with pytest.raises(StepFailed, match="too many version conflicts"):
        await wf.update_entity("t", n, {"s": "z"}, phase="p2", retries=2)
