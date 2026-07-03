"""The per-item registry of entity types (#419 §D discovery).

An `EntityType` bundles a type's schema, its skeleton, and where its records
live. `EntityCatalog` is the resolved set of types for one item — built by
scanning `.entity/<type>/`, or constructed directly in tests. An App with no
`.entity/` dir yields an empty catalog, so the item behaves exactly as before.
"""

from __future__ import annotations

import msgspec
import yaml

from ..filestore.protocol import FileStore
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
        fields.append(
            FieldSpec(
                name=str(fname),
                role=role,
                required=bool(spec.get("required", False)),
                values=spec.get("values"),
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


async def discover_catalog(
    store: FileStore, workspace_id: str
) -> tuple[EntityCatalog, list[Diagnostic]]:
    """Scan `.entity/<type>/` into the item's `EntityCatalog`. No `.entity/`
    dir → empty catalog (opt-in guard)."""
    paths = await store.ls(workspace_id, prefix=_ENTITY_ROOT)
    type_names = sorted(
        {p[len(_ENTITY_ROOT) :].split("/", 1)[0] for p in paths if "/" in p[len(_ENTITY_ROOT) :]}
    )
    types: dict[str, EntityType] = {}
    diagnostics: list[Diagnostic] = []
    for name in type_names:
        schema_path = f"{_ENTITY_ROOT}{name}/schema.yaml"
        if not await store.exists(workspace_id, schema_path):
            continue
        skeleton_path = f"{_ENTITY_ROOT}{name}/skeleton.md"
        skeleton = ""
        if await store.exists(workspace_id, skeleton_path):
            skeleton = (await store.read(workspace_id, skeleton_path)).decode("utf-8", "replace")
        entity_type, diags = _load_type(name, await store.read(workspace_id, schema_path), skeleton)
        diagnostics.extend(diags)
        if entity_type is not None:
            types[name] = entity_type
    return EntityCatalog(types), diagnostics
