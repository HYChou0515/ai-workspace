"""`read_entity_records` — entities read from a workspace directory (#847/#848).

A view plugin's sandbox half sees the workspace as files, not through the API.
The oracle is `EntityStore.query` over a `MemoryFileStore` holding the SAME
files: what a chart gets from `source: {entity: …}` must be what the table view
of that type shows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workspace_app.entity.catalog import discover_catalog
from workspace_app.entity.local import read_entity_records
from workspace_app.entity.store import EntityStore
from workspace_app.filestore.memory import MemoryFileStore

FILES: dict[str, str] = {
    "/.entity/issue/schema.yaml": (
        "path: issues\n"
        "fields:\n"
        "  title: { role: text, required: true }\n"
        "  status: { role: status, values: [open, done] }\n"
        "  milestone: { role: ref, to: milestone }\n"
        "  progress: { role: progress }\n"
    ),
    "/.entity/issue/skeleton.md": "---\ntitle: {{arg.title}}\nstatus: open\n---\n",
    "/.entity/milestone/schema.yaml": (
        "path: plan/milestones\n"
        "fields:\n"
        "  title: { role: text }\n"
        "  issues: { role: backref, from: issue.milestone }\n"
        "  avg: { role: rollup, over: issues, agg: avg, field: progress }\n"
    ),
    "/issues/1.md": "---\ntitle: Login\nstatus: open\nmilestone: 1\nprogress: 50\n---\nbody\n",
    "/issues/2.md": "---\ntitle: Logout\nstatus: done\nmilestone: 1\nprogress: 100\n---\n",
    "/issues/3.md": "---\ntitle: [unclosed\n---\n",  # frontmatter that does not parse: dropped
    "/issues/10.md": "---\ntitle: Ten\nstatus: open\n---\n",
    "/issues/notes.txt": "not a record",
    "/issues/4.md.bak": "---\ntitle: backup\n---\n",
    "/plan/milestones/1.md": "---\ntitle: M1\n---\n",
}


async def _oracle(type_name: str) -> list[dict]:
    fs = MemoryFileStore()
    for path, text in FILES.items():
        await fs.write("ws", path, text.encode())
    catalog, _ = await discover_catalog(fs, "ws")
    result = await EntityStore(fs, "ws", catalog).query(type_name)
    return [{"number": e.number, **e.fields} for e in result.entities]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for path, text in FILES.items():
        target = tmp_path / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return tmp_path


@pytest.mark.parametrize("type_name", ["issue", "milestone"])
def test_records_match_the_store(workspace: Path, type_name: str):
    expected = asyncio.run(_oracle(type_name))
    assert expected, "the fixture should give the oracle records to compare"
    assert read_entity_records(workspace, type_name) == expected


def test_the_fixture_exercises_what_it_claims():
    issues = asyncio.run(_oracle("issue"))
    assert [r["number"] for r in issues] == [1, 2, 10]  # 3 dropped, junk ignored
    [m1] = asyncio.run(_oracle("milestone"))
    assert m1["issues"] == [1, 2] and m1["avg"] == 75


def test_an_unknown_type_is_named_with_the_types_there_are(workspace: Path):
    with pytest.raises(LookupError, match=r"'task'.*issue, milestone"):
        read_entity_records(workspace, "task")


def test_a_missing_file_reads_as_the_store_says_it(tmp_path: Path):
    # The catalog's tolerant read (a schema gone since the listing) catches the
    # protocol's FileNotFound, not the OS error.
    from workspace_app.entity.local import DirFileStore
    from workspace_app.filestore.protocol import FileNotFound

    (tmp_path / "adir").mkdir()
    for path in ("/nope.md", "/adir"):
        with pytest.raises(FileNotFound):
            asyncio.run(DirFileStore(tmp_path).read("local", path))


def test_a_workspace_without_entities_has_none(tmp_path: Path):
    with pytest.raises(LookupError, match="no entity types"):
        read_entity_records(tmp_path, "issue")
