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
