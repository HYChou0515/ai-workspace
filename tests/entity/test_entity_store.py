"""Behaviour of the entity CRUD spine, through the public `EntityStore`
interface (#419 P1). A file-first entity is a workspace file with parsed
frontmatter; the store allocates its permanent number, renders its skeleton,
and reads it back — internals (schema/parser/numbering) stay swappable."""

from __future__ import annotations

from workspace_app.entity.catalog import EntityCatalog, EntityType
from workspace_app.entity.schema import EntitySchema, FieldSpec, Role
from workspace_app.entity.store import EntityStore
from workspace_app.filestore.memory import MemoryFileStore


def _issue_type() -> EntityType:
    schema = EntitySchema(
        fields=[
            FieldSpec(name="title", role=Role.TEXT, required=True),
            FieldSpec(name="status", role=Role.STATUS, values=["open", "done"]),
        ]
    )
    skeleton = "---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"
    return EntityType(name="issue", schema=schema, skeleton=skeleton, records_path="issues")


def _store(fs: MemoryFileStore | None = None) -> EntityStore:
    catalog = EntityCatalog({"issue": _issue_type()})
    return EntityStore(fs or MemoryFileStore(), "ws1", catalog)


async def test_create_allocates_number_one_and_reads_back() -> None:
    """First create on an empty store gets permanent number 1; getting it back
    parses the frontmatter into fields (the skeleton default `status: open`)."""
    store = _store()

    created = await store.create(
        "issue", {"title": "Login broken"}, actor="alice", now="2026-07-03"
    )

    assert created.number == 1
    got = await store.get("issue", 1)
    assert got.fields["title"] == "Login broken"
    assert got.fields["status"] == "open"


async def test_query_scans_and_projects_all_records() -> None:
    """`query` scans the records dir (no index — §S2) and projects every record
    into a parsed entity, ordered by number."""
    store = _store()
    await store.create("issue", {"title": "A"}, actor="alice", now="2026-07-03")
    await store.create("issue", {"title": "B"}, actor="alice", now="2026-07-03")

    result = await store.query("issue")

    assert [e.number for e in result.entities] == [1, 2]
    assert [e.fields["title"] for e in result.entities] == ["A", "B"]


async def test_query_degrades_one_broken_entity_without_dropping_the_rest() -> None:
    """A hand-edited entity with broken frontmatter (§C escape hatch) drops out
    of the projection but surfaces as an invalid entity with an error
    diagnostic — the rest still render (§E warning-not-death)."""
    fs = MemoryFileStore()
    store = _store(fs)
    await store.create("issue", {"title": "A"}, actor="alice", now="2026-07-03")
    await fs.write("ws1", "/issues/2.md", b"just some notes, no frontmatter at all")

    result = await store.query("issue")

    assert [e.number for e in result.entities] == [1]
    assert [e.number for e in result.invalid] == [2]
    assert any(d.level == "error" for d in result.invalid[0].diagnostics)


async def test_update_patches_one_field_and_preserves_body_and_number() -> None:
    """`update` changes only the patched field; number, body, and untouched
    fields survive (the UI drag/cell-edit and the agent share this path)."""
    store = _store()
    await store.create(
        "issue", {"title": "A", "body": "repro steps"}, actor="alice", now="2026-07-03"
    )

    updated = await store.update("issue", 1, {"status": "done"})

    assert updated.number == 1
    assert updated.fields["status"] == "done"
    assert updated.fields["title"] == "A"
    got = await store.get("issue", 1)
    assert got.fields["status"] == "done"
    assert got.body.strip() == "repro steps"


async def test_update_with_current_version_succeeds() -> None:
    """§C6: passing the `version` you just read lets the write through — nothing
    changed in between."""
    store = _store()
    created = await store.create("issue", {"title": "A"}, actor="alice", now="2026-07-03")

    updated = await store.update("issue", 1, {"status": "done"}, expected_version=created.version)

    assert updated.fields["status"] == "done"
    assert updated.version != created.version  # the content moved, so the token flips


async def test_update_against_a_stale_version_conflicts_and_does_not_write() -> None:
    """§C6: a write against the version read BEFORE a concurrent edit is rejected
    with `EntityConflict`, and the concurrent edit is preserved (no lost update)."""
    import pytest

    from workspace_app.entity.store import EntityConflict

    store = _store()
    created = await store.create("issue", {"title": "A"}, actor="alice", now="2026-07-03")
    stale = created.version
    # a concurrent writer changes the record → its version moves on
    await store.update("issue", 1, {"status": "done"})

    with pytest.raises(EntityConflict):
        await store.update("issue", 1, {"title": "clobbered"}, expected_version=stale)

    # the concurrent edit survived; the rejected patch never landed
    got = await store.get("issue", 1)
    assert got.fields["status"] == "done"
    assert got.fields["title"] == "A"


async def test_status_outside_closed_vocab_lints_but_is_not_blocked() -> None:
    """A `status` outside the schema's closed values is written anyway (§C7
    lint-not-block) and surfaces a *warning* — not an error, so it still
    projects into the view."""
    store = _store()
    await store.create("issue", {"title": "A"}, actor="alice", now="2026-07-03")

    updated = await store.update("issue", 1, {"status": "frozen"})

    assert updated.fields["status"] == "frozen"
    assert updated.ok
    assert any(d.level == "warning" and d.field == "status" for d in updated.diagnostics)
    result = await store.query("issue")
    assert [e.number for e in result.entities] == [1]


async def test_health_flattens_warnings_and_errors_across_types() -> None:
    """§E3: `health` collects every finding — a lint warning on a projecting
    record and a parse error on a dropped one — flattened for the health view."""
    fs = MemoryFileStore()
    store = _store(fs)
    await store.create("issue", {"title": "A"}, actor="a", now="d")
    await store.update("issue", 1, {"status": "frozen"})  # outside vocab → warning
    await fs.write("ws1", "/issues/2.md", b"broken, no frontmatter")  # → error

    findings = await store.health()

    seen = {(f.number, f.level) for f in findings}
    assert (1, "warning") in seen
    assert (2, "error") in seen


async def test_health_is_empty_when_every_record_is_clean() -> None:
    store = _store()
    await store.create("issue", {"title": "A"}, actor="a", now="d")
    assert await store.health() == []


async def test_hard_delete_of_top_record_never_reissues_its_number() -> None:
    """Users can hard-delete an entity file; the high-water counter in
    `.readonly/` still advances, so a deleted top number is never reissued
    (§N2 never-reuse) — refs to it can't silently point to a new record."""
    fs = MemoryFileStore()
    store = _store(fs)
    for _ in range(3):
        await store.create("issue", {"title": "x"}, actor="a", now="d")
    await fs.delete("ws1", "/issues/3.md")

    created = await store.create("issue", {"title": "y"}, actor="a", now="d")

    assert created.number == 4


async def test_non_numeric_files_in_records_dir_are_ignored() -> None:
    """A stray non-`N.md` file in the records dir is not a record — it doesn't
    project and doesn't perturb numbering."""
    fs = MemoryFileStore()
    store = _store(fs)
    await fs.write("ws1", "/issues/README.md", b"just notes")

    created = await store.create("issue", {"title": "A"}, actor="a", now="d")

    assert created.number == 1
    result = await store.query("issue")
    assert [e.number for e in result.entities] == [1]


async def test_query_includes_computed_backref_and_rollup() -> None:
    """A projected read resolves relational fields compute-on-read: the
    milestone's backref `issues` and its `avg` rollup over their progress."""
    issue = EntityType(
        name="issue",
        schema=EntitySchema(
            fields=[
                FieldSpec("milestone", Role.REF, to="milestone"),
                FieldSpec("progress", Role.PROGRESS),
            ]
        ),
        skeleton="---\nmilestone: {{arg.milestone}}\nprogress: {{arg.progress}}\n---\n",
        records_path="issues",
    )
    milestone = EntityType(
        name="milestone",
        schema=EntitySchema(
            fields=[
                FieldSpec("title", Role.TEXT),
                FieldSpec("issues", Role.BACKREF, from_="issue.milestone"),
                FieldSpec("avg", Role.ROLLUP, over="issues", agg="avg", field="progress"),
            ]
        ),
        skeleton="---\ntitle: {{arg.title}}\n---\n",
        records_path="milestones",
    )
    store = EntityStore(
        MemoryFileStore(), "ws1", EntityCatalog({"issue": issue, "milestone": milestone})
    )
    await store.create("milestone", {"title": "M1"}, actor="a", now="d")
    await store.create("issue", {"milestone": 1, "progress": 50}, actor="a", now="d")
    await store.create("issue", {"milestone": 1, "progress": 100}, actor="a", now="d")

    result = await store.query("milestone")

    milestone_row = result.entities[0]
    assert milestone_row.fields["issues"] == [1, 2]
    assert milestone_row.fields["avg"] == 75
