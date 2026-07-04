"""The WorkflowHandle file/IO surface (manual §3) — json round-trip + glob."""

from workspace_app.workflow.handle import WorkflowHandle


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


async def test_create_entity_uses_framework_numbering_and_dedups_by_site() -> None:
    """`wf.create_entity` mints entities through the SAME EntityStore path as the
    UI/agent (framework numbering, no raw wf.write). Its dedup identity is the
    capability's ``name`` (its site, #435 P2): distinct sites mint distinct entities;
    the same site re-run self-dedups instead of duplicating."""
    import pytest

    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.workflow.engine import StepFailed

    store = MemoryFileStore()
    await store.write(
        "ws", "/.entity/issue/schema.yaml", b"path: issues\nfields:\n  title: {role: text}\n"
    )
    await store.write("ws", "/.entity/issue/skeleton.md", b"---\ntitle: {{arg.title}}\n---\n")
    wf = WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm", user="alice")

    n1 = await wf.create_entity("issue", {"title": "A"}, name="a")
    n2 = await wf.create_entity("issue", {"title": "B"}, name="b")
    assert (n1, n2) == (1, 2)
    assert "title: A" in (await store.read("ws", "/issues/1.md")).decode()

    # the same site re-run → self-dedup, no duplicate file
    again = await wf.create_entity("issue", {"title": "A"}, name="a")
    assert again == 1
    assert not await store.exists("ws", "/issues/3.md")

    # unknown type fails loudly (before journaling)
    with pytest.raises(StepFailed):
        await wf.create_entity("nope", {"title": "X"}, name="c")
