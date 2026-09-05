"""Entity-type discovery (#419 §D). Scanning `.entity/<type>/{schema.yaml,
skeleton.md}` yields the item's `EntityCatalog`. Opt-in guard: no `.entity/`
dir → empty catalog → the item behaves exactly as before."""

from __future__ import annotations

from workspace_app.entity.catalog import discover_catalog
from workspace_app.filestore.memory import MemoryFileStore

_SCHEMA = b"""\
path: issues
fields:
  title: { role: text, required: true }
  status: { role: status, values: [open, done] }
"""
_SKELETON = b"---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"


async def test_discovers_entity_type_from_dot_entity_dir() -> None:
    fs = MemoryFileStore()
    await fs.write("ws1", "/.entity/issue/schema.yaml", _SCHEMA)
    await fs.write("ws1", "/.entity/issue/skeleton.md", _SKELETON)

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    assert "issue" in catalog
    entity_type = catalog.get("issue")
    assert entity_type.records_path == "issues"
    assert [f.name for f in entity_type.schema.fields] == ["title", "status"]
    status = entity_type.schema.field("status")
    assert status is not None and status.values == ["open", "done"]
    assert entity_type.skeleton.startswith("---")
    assert diagnostics == []


async def test_no_dot_entity_dir_yields_empty_catalog() -> None:
    """Opt-in guard: an App/item with no `.entity/` sees no entity behavior."""
    catalog, diagnostics = await discover_catalog(MemoryFileStore(), "ws1")

    assert not catalog
    assert diagnostics == []


async def test_broken_schema_degrades_only_that_type() -> None:
    """A broken `schema.yaml` drops just its own type (§E schema degradation)
    with an error diagnostic; sibling types still load."""
    fs = MemoryFileStore()
    await fs.write("ws1", "/.entity/issue/schema.yaml", _SCHEMA)
    await fs.write("ws1", "/.entity/issue/skeleton.md", _SKELETON)
    await fs.write("ws1", "/.entity/milestone/schema.yaml", b"- not\n- a\n- mapping\n")

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    assert "issue" in catalog
    assert "milestone" not in catalog
    assert any(d.level == "error" and d.field == "milestone" for d in diagnostics)


async def test_type_without_skeleton_loads_with_empty_skeleton() -> None:
    fs = MemoryFileStore()
    await fs.write(
        "ws1", "/.entity/note/schema.yaml", b"path: notes\nfields:\n  title: {role: text}\n"
    )

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    assert catalog.names() == ["note"]
    assert catalog.get("note").skeleton == ""


async def test_malformed_schema_yaml_degrades_the_type() -> None:
    fs = MemoryFileStore()
    await fs.write("ws1", "/.entity/bad/schema.yaml", b"fields: [unclosed\n")

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    assert "bad" not in catalog
    assert any(d.level == "error" for d in diagnostics)


async def test_unknown_role_falls_back_to_text_with_a_warning() -> None:
    fs = MemoryFileStore()
    await fs.write("ws1", "/.entity/x/schema.yaml", b"fields:\n  weird: {role: nonsense}\n")

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    field = catalog.get("x").schema.field("weird")
    assert field is not None and field.role.value == "text"
    assert any(d.level == "warning" for d in diagnostics)


async def test_type_dir_without_schema_is_skipped() -> None:
    """A `.entity/<type>/` dir carrying only a skeleton (no `schema.yaml`) is
    not a usable type — it's skipped, not a crash."""
    fs = MemoryFileStore()
    await fs.write("ws1", "/.entity/orphan/skeleton.md", b"---\n---\n")

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    assert "orphan" not in catalog
    assert not catalog


async def test_loads_relational_role_config() -> None:
    """Relational roles (§A) carry their wiring: ref `to`, backref `from`,
    rollup `over`/`agg`/`field`/`where`."""
    fs = MemoryFileStore()
    await fs.write(
        "ws1",
        "/.entity/milestone/schema.yaml",
        b"path: milestones\n"
        b"fields:\n"
        b"  title: { role: text }\n"
        b"  span: { role: daterange }\n"
        b"  epic: { role: ref, to: epic }\n"
        b"  issues: { role: backref, from: issue.milestone }\n"
        b"  progress: { role: rollup, over: issues, agg: avg, field: progress }\n"
        b"  done: { role: rollup, over: issues, agg: count, where: { status: done } }\n",
    )

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    schema = catalog.get("milestone").schema
    span = schema.field("span")
    assert span is not None and span.role.value == "daterange"
    epic = schema.field("epic")
    assert epic is not None and epic.to == "epic"
    issues = schema.field("issues")
    assert issues is not None and issues.from_ == "issue.milestone"
    progress = schema.field("progress")
    assert progress is not None
    assert (progress.over, progress.agg, progress.field) == ("issues", "avg", "progress")
    done = schema.field("done")
    assert done is not None and done.where == {"status": "done"}
    assert diagnostics == []


async def test_loads_status_colors_map() -> None:
    """A `status`/`select` field may pin each value to a semantic hue via a
    `colors:` map (#GH-projects B) — the renderer's coloured chip reads it;
    a field without one stays None (auto-hashed)."""
    fs = MemoryFileStore()
    await fs.write(
        "ws1",
        "/.entity/issue/schema.yaml",
        b"path: issues\n"
        b"fields:\n"
        b"  title: { role: text }\n"
        b"  status:\n"
        b"    role: status\n"
        b"    values: [open, done]\n"
        b"    colors: { open: blue, done: green }\n",
    )

    catalog, diagnostics = await discover_catalog(fs, "ws1")

    schema = catalog.get("issue").schema
    status = schema.field("status")
    assert status is not None and status.colors == {"open": "blue", "done": "green"}
    # a field with no colors map stays None (never {} — the FE distinguishes them).
    title = schema.field("title")
    assert title is not None and title.colors is None
    assert diagnostics == []


async def _ship_types(files, count: int) -> None:
    for i in range(count):
        await files.write("ws1", f"/.entity/t{i}/schema.yaml", _SCHEMA)
        await files.write("ws1", f"/.entity/t{i}/skeleton.md", _SKELETON)


async def test_discovery_cost_does_not_scale_with_how_many_types() -> None:
    """Discovery is ONE operation, so settling where the workspace lives is paid
    once — not twice per type for the two `exists` questions and twice more for
    the two reads.

    Whether a type's `schema.yaml` / `skeleton.md` are there falls straight out
    of the listing discovery already has, exactly as `workspace_skill_metas`
    derives which folders are copies; asking the store again is a round trip
    spent re-learning what we were just told."""
    from tests.warm_workspace import warm_files

    few, sb_few = await warm_files()
    await _ship_types(few, 2)
    sb_few.liveness_probes = 0
    await discover_catalog(few, "ws1")
    probes_few = sb_few.liveness_probes

    many, sb_many = await warm_files()
    await _ship_types(many, 8)
    sb_many.liveness_probes = 0
    await discover_catalog(many, "ws1")
    probes_many = sb_many.liveness_probes

    assert probes_many == probes_few, (
        f"{probes_few} probes for 2 types but {probes_many} for 8 — discovery's "
        "cost is scaling with how many types the item declares"
    )


async def test_a_type_that_vanishes_after_the_listing_does_not_empty_the_catalog() -> None:
    """Discovery used to ask `exists` for each type's schema immediately before
    reading it, so a type deleted mid-scan was skipped and the rest of the
    catalog still loaded. Reading the whole set in one batch widened that window
    — one listing at the top, the reads at the bottom — so the tolerance has to
    be stated here rather than inherited from a check that no longer happens.

    An empty catalog is not a small matter: with no types, the item's entity
    views have nothing to render, over intact data."""
    from workspace_app.files.facade import WorkspaceFiles
    from workspace_app.filestore.memory import MemoryFileStore

    class _GhostListing(WorkspaceFiles):
        async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
            got = await super().ls(workspace_id, prefix)
            return [*got, "/.entity/ghost/schema.yaml"]  # listed, never written

    files = _GhostListing(MemoryFileStore())
    await files.write("ws1", "/.entity/issue/schema.yaml", _SCHEMA)
    await files.write("ws1", "/.entity/issue/skeleton.md", _SKELETON)

    catalog, _diags = await discover_catalog(files, "ws1")

    assert catalog.names() == ["issue"]
