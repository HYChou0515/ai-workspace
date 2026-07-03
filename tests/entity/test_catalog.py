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
