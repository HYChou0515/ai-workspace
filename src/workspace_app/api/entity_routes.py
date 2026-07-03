"""Entity CRUD routes (#419).

The per-item HTTP surface for the file-first entity framework: list the item's
entity types (schema + quick-create form), query a type's records (scan +
compute-on-read projection), and create/update records through the single
`EntityStore` write path. All opt-in — an item with no `.entity/` dir has an
empty catalog, so every endpoint is a safe no-op there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, FastAPI, HTTPException
from specstar import SpecStar

from ..entity.catalog import EntityCatalog, discover_catalog
from ..entity.forms import form_spec
from ..entity.parser import ParsedEntity
from ..entity.schema import Role
from ..entity.store import EntityConflict, EntityStore
from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..users import UserDirectory
from .activity import ActivityLog
from .locator import ItemLocator
from .notifications import notify
from .schemas import (
    _EntityCatalogOut,
    _EntityCreateBody,
    _EntityDiagnostic,
    _EntityFieldSpec,
    _EntityFormField,
    _EntityHealthFinding,
    _EntityHealthOut,
    _EntityListOut,
    _EntityOut,
    _EntityTypeOut,
    _EntityUpdateBody,
)


def _diag(entity: ParsedEntity) -> list[_EntityDiagnostic]:
    return [
        _EntityDiagnostic(level=d.level, message=d.message, field=d.field)
        for d in entity.diagnostics
    ]


def _entity_out(entity: ParsedEntity) -> _EntityOut:
    return _EntityOut(
        number=entity.number,
        type_name=entity.type_name,
        fields=entity.fields,
        body=entity.body,
        diagnostics=_diag(entity),
        version=entity.version,
    )


def register_entity_routes(
    app: FastAPI | APIRouter,
    *,
    files: WorkspaceFiles,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
    activity: ActivityLog,
    spec: SpecStar,
    users: UserDirectory,
) -> None:
    """Mount the entity CRUD routes onto ``app``.

    ``activity`` records create/update events into the shared feed (§F3); ``spec``
    + ``users`` drive assignment notifications — when a record's ``actor``-role
    field is set to a person, they're notified (§F2)."""

    # Shared per-(item,type) numbering locks, so racing creates across requests on
    # one pod can't both claim a number (single-pod serialization, §N5).
    locks: dict[str, asyncio.Lock] = {}

    async def _store(slug: str, item_id: str) -> tuple[str, EntityStore]:
        investigation_id = locator.require_item(slug, item_id)
        catalog, _diags = await discover_catalog(files, investigation_id)
        return investigation_id, EntityStore(files, investigation_id, catalog, locks=locks)

    def _require_type(catalog: EntityCatalog, type_name: str) -> None:
        if type_name not in catalog:
            raise HTTPException(status_code=404, detail=f"unknown entity type: {type_name}")

    def _record(iid: str, kind: str, type_name: str, number: int) -> None:
        activity.record(
            kind,
            f"{kind.split('_')[-1].title()} {type_name} #{number}",
            {"investigation_id": iid, "type": type_name, "number": number},
        )

    def _notify_assignees(
        store: EntityStore, type_name: str, number: int, entity: ParsedEntity, changed: set[str]
    ) -> None:
        """Notify each person freshly named in an ``actor``-role field that just
        changed (§F2). Handles resolve through the company directory; an unknown
        handle or self-assignment is silently skipped (lint-not-block)."""
        actor = get_user_id()
        schema = store.catalog.get(type_name).schema
        for spec_field in schema.fields:
            if spec_field.role is not Role.ACTOR or spec_field.name not in changed:
                continue
            value = entity.fields.get(spec_field.name)
            if not value:
                continue
            person = users.find_by_handle(str(value))
            if person is None or person.id == actor:
                continue
            notify(
                spec,
                recipient=person.id,
                kind="entity_assignment",
                title=f"You were assigned {type_name} #{number}",
                actor=actor,
            )

    @app.get("/a/{slug}/items/{item_id}/entities")
    async def list_entity_types(slug: str, item_id: str) -> _EntityCatalogOut:
        investigation_id = locator.require_item(slug, item_id)
        catalog, diagnostics = await discover_catalog(files, investigation_id)
        types = []
        for name in catalog.names():
            entity_type = catalog.get(name)
            types.append(
                _EntityTypeOut(
                    name=name,
                    records_path=entity_type.records_path,
                    fields=[
                        _EntityFieldSpec(
                            name=f.name,
                            role=f.role.value,
                            required=f.required,
                            values=f.values,
                            to=f.to,
                            **{"from": f.from_},
                            over=f.over,
                            agg=f.agg,
                            field=f.field,
                            where=f.where,
                        )
                        for f in entity_type.schema.fields
                    ],
                    form=[
                        _EntityFormField(
                            name=w.name, widget=w.widget, required=w.required, values=w.values
                        )
                        for w in form_spec(entity_type)
                    ],
                )
            )
        return _EntityCatalogOut(
            types=types,
            diagnostics=[
                _EntityDiagnostic(level=d.level, message=d.message, field=d.field)
                for d in diagnostics
            ],
        )

    @app.get("/a/{slug}/items/{item_id}/entity_health")
    async def entity_health(slug: str, item_id: str) -> _EntityHealthOut:
        """The project-health view's data (§E3) — every parser/lint finding across
        all of the item's entity types. A distinct path (not `entities/health`) so
        it can't be mistaken for a type named "health"."""
        _iid, store = await _store(slug, item_id)
        findings = await store.health()
        return _EntityHealthOut(
            findings=[
                _EntityHealthFinding(
                    type_name=f.type_name,
                    number=f.number,
                    level=f.level,
                    message=f.message,
                    field=f.field,
                )
                for f in findings
            ]
        )

    @app.get("/a/{slug}/items/{item_id}/entities/{type_name}")
    async def query_entities(slug: str, item_id: str, type_name: str) -> _EntityListOut:
        _iid, store = await _store(slug, item_id)
        _require_type(store.catalog, type_name)
        result = await store.query(type_name)
        return _EntityListOut(
            entities=[_entity_out(e) for e in result.entities],
            invalid=[_entity_out(e) for e in result.invalid],
        )

    @app.post("/a/{slug}/items/{item_id}/entities/{type_name}")
    async def create_entity(
        slug: str, item_id: str, type_name: str, body: _EntityCreateBody
    ) -> _EntityOut:
        iid, store = await _store(slug, item_id)
        _require_type(store.catalog, type_name)
        created = await store.create(
            type_name, body.args, actor=get_user_id(), now=datetime.now(UTC).date().isoformat()
        )
        _record(iid, "entity_created", type_name, created.number)
        _notify_assignees(store, type_name, created.number, created, set(body.args))
        return _entity_out(created)

    @app.put("/a/{slug}/items/{item_id}/entities/{type_name}/{number}")
    async def update_entity(
        slug: str, item_id: str, type_name: str, number: int, body: _EntityUpdateBody
    ) -> _EntityOut:
        iid, store = await _store(slug, item_id)
        _require_type(store.catalog, type_name)
        try:
            updated = await store.update(
                type_name, number, body.patch, expected_version=body.expected_version
            )
        except FileNotFound as e:
            raise HTTPException(status_code=404, detail=f"no {type_name} #{number}") from e
        except EntityConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        _record(iid, "entity_updated", type_name, number)
        _notify_assignees(store, type_name, number, updated, set(body.patch))
        return _entity_out(updated)
