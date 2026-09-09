"""The per-item registry of entity types (#419 §D discovery).

An `EntityType` bundles a type's schema, its skeleton, and where its records
live. `EntityCatalog` is the resolved set of types for one item — built by
scanning `.entity/<type>/`, or constructed directly in tests. An App with no
`.entity/` dir yields an empty catalog, so the item behaves exactly as before.
"""

from __future__ import annotations

import contextlib

import msgspec
import yaml

from ..filestore.protocol import FileNotFound, FileStore
from .diagnostics import Diagnostic
from .schema import EntitySchema, FieldSpec, Role


class EntityType(msgspec.Struct, frozen=True):
    name: str
    schema: EntitySchema
    skeleton: str
    records_path: str
    """Workspace dir holding the records, e.g. `issues` → `/issues/5.md`."""


class EntityCatalog:
    def __init__(self, types: dict[str, EntityType]) -> None:
        self._types = dict(types)

    def get(self, name: str) -> EntityType:
        return self._types[name]

    def names(self) -> list[str]:
        return list(self._types)

    def __contains__(self, name: str) -> bool:
        return name in self._types

    def __bool__(self) -> bool:
        return bool(self._types)


_ENTITY_ROOT = "/.entity/"


def _load_type(
    name: str, schema_bytes: bytes, skeleton: str
) -> tuple[EntityType | None, list[Diagnostic]]:
    """Build one `EntityType` from its `schema.yaml`. A broken schema drops the
    whole type (§E schema degradation) rather than raising."""
    try:
        doc = yaml.safe_load(schema_bytes)
    except yaml.YAMLError as e:
        return None, [Diagnostic("error", f"entity type {name!r}: bad schema.yaml: {e}", name)]
    if not isinstance(doc, dict):
        return None, [
            Diagnostic("error", f"entity type {name!r}: schema.yaml is not a mapping", name)
        ]
    diagnostics: list[Diagnostic] = []
    fields: list[FieldSpec] = []
    for fname, raw in (doc.get("fields") or {}).items():
        spec = raw or {}
        try:
            role = Role(str(spec.get("role", "text")))
        except ValueError:
            diagnostics.append(
                Diagnostic("warning", f"{name}.{fname}: unknown role, treated as text", str(fname))
            )
            role = Role.TEXT
        where = spec.get("where")
        colors = spec.get("colors")
        fields.append(
            FieldSpec(
                name=str(fname),
                role=role,
                required=bool(spec.get("required", False)),
                values=spec.get("values"),
                colors={str(k): str(v) for k, v in colors.items()}
                if isinstance(colors, dict)
                else None,
                to=spec.get("to"),
                from_=spec.get("from"),
                over=spec.get("over"),
                agg=spec.get("agg"),
                field=spec.get("field"),
                where={str(k): str(v) for k, v in where.items()}
                if isinstance(where, dict)
                else None,
            )
        )
    records_path = str(doc.get("path", name))
    entity_type = EntityType(
        name=name, schema=EntitySchema(fields=fields), skeleton=skeleton, records_path=records_path
    )
    return entity_type, diagnostics


async def load_entity_type(
    store: FileStore, workspace_id: str, name: str
) -> tuple[EntityCatalog, list[Diagnostic]]:
    """One named type's entry, without discovering the item's other types.

    A request that names its type — create, update, query — never looks at the
    others, but rebuilding the whole catalog read every declared type's schema
    AND skeleton first. On a PM item that is half the work spent on a type the
    request does not mention.

    It also drops the ``exists`` before each ``read``: the read itself reports
    absence. In production these go through the sandbox, and each one is preceded
    by a liveness probe averaging 1.4s, so a redundant existence check is not
    free the way it looks.

    An absent schema yields an EMPTY catalog rather than an error, exactly as the
    full scan does — the route then answers "unknown entity type" as before."""
    try:
        raw = await store.read(workspace_id, f"{_ENTITY_ROOT}{name}/schema.yaml")
    except FileNotFound:
        return EntityCatalog({}), []
    skeleton = ""
    with contextlib.suppress(FileNotFound):
        skeleton_bytes = await store.read(workspace_id, f"{_ENTITY_ROOT}{name}/skeleton.md")
        skeleton = skeleton_bytes.decode("utf-8", "replace")
    entity_type, diagnostics = _load_type(name, raw, skeleton)
    return EntityCatalog({name: entity_type} if entity_type is not None else {}), diagnostics


async def discover_catalog(
    store: FileStore, workspace_id: str
) -> tuple[EntityCatalog, list[Diagnostic]]:
    """Scan `.entity/<type>/` into the item's `EntityCatalog`. No `.entity/`
    dir → empty catalog (opt-in guard)."""
    from ..files.facade import read_all_existing

    paths = await store.ls(workspace_id, prefix=_ENTITY_ROOT)
    # Whether a type's files are there falls straight out of the listing we
    # already have — the trick `workspace_skill_metas` uses to know which skill
    # folders are copies. Asking `exists` per type was a round trip spent
    # re-learning what the listing just said, and there were TWO of them per
    # type before the two reads.
    present = set(paths)
    type_names = sorted(
        {p[len(_ENTITY_ROOT) :].split("/", 1)[0] for p in paths if "/" in p[len(_ENTITY_ROOT) :]}
    )
    wanted: list[tuple[str, str, str | None]] = []
    to_read: list[str] = []
    for name in type_names:
        schema_path = f"{_ENTITY_ROOT}{name}/schema.yaml"
        if schema_path not in present:
            continue
        skeleton_path = f"{_ENTITY_ROOT}{name}/skeleton.md"
        has_skeleton = skeleton_path in present
        wanted.append((name, schema_path, skeleton_path if has_skeleton else None))
        to_read.append(schema_path)
        if has_skeleton:
            to_read.append(skeleton_path)

    # Tolerant, because the `exists`-per-type this replaced was: a type whose
    # `schema.yaml` disappeared between the listing and here was skipped, and
    # the rest of the catalog still loaded. Batching widened that window (one
    # listing at the top, the reads at the bottom), so the tolerance matters
    # MORE than it did, not less.
    blob = await read_all_existing(store, workspace_id, to_read)
    types: dict[str, EntityType] = {}
    diagnostics: list[Diagnostic] = []
    for name, schema_path, skeleton_path in wanted:
        if schema_path not in blob:
            continue  # vanished since the listing — skip the type, keep the rest
        skeleton = (
            blob[skeleton_path].decode("utf-8", "replace")
            if skeleton_path is not None and skeleton_path in blob
            else ""
        )
        entity_type, diags = _load_type(name, blob[schema_path], skeleton)
        diagnostics.extend(diags)
        if entity_type is not None:
            types[name] = entity_type
    return EntityCatalog(types), diagnostics
