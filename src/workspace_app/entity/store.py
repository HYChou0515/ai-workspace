"""`EntityStore` — the single write/read path for entities (#419 §C).

The one interface the UI, the agent tools, and workflows all call. `create`
allocates the permanent number, renders the skeleton, and writes the file;
`get`/`query` scan-and-parse (no index — §S2). Numbering is serialized per
(item, type) by an in-process lock (single-pod guarantee — N5).
"""

from __future__ import annotations

import asyncio
from typing import Any

import msgspec

from ..filestore.protocol import FileStore
from .catalog import EntityCatalog
from .numbering import allocate
from .parser import ParsedEntity, parse_entity, serialize_entity
from .projection import Corpus, compute_derived
from .schema import Role
from .skeleton import render_skeleton


class EntityConflict(Exception):
    """Raised by `update` when `expected_version` doesn't match the record's
    current content — the record changed since the caller read it (§C6). The
    caller re-reads and retries with the fresh version."""


class QueryResult(msgspec.Struct):
    entities: list[ParsedEntity]
    """Records that parsed cleanly — the projection the views render."""
    invalid: list[ParsedEntity]
    """Records dropped from the projection (an `error` diagnostic), kept so the
    project-health view can list them (§E)."""


def _record_number(path: str) -> int | None:
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    return int(stem) if stem.isdigit() and path.endswith(".md") else None


class EntityStore:
    def __init__(
        self,
        filestore: FileStore,
        workspace_id: str,
        catalog: EntityCatalog,
        *,
        locks: dict[str, asyncio.Lock] | None = None,
    ) -> None:
        self._fs = filestore
        self._ws = workspace_id
        self._catalog = catalog
        # Numbering must serialize per (item, type). Pass a shared registry when
        # constructing per-request stores (the API) so racing creates on one item
        # can't both claim a number; the default is fine for a single store.
        self._locks = locks if locks is not None else {}

    @property
    def catalog(self) -> EntityCatalog:
        return self._catalog

    def _record_path(self, records_path: str, number: int) -> str:
        return f"/{records_path}/{number}.md"

    async def create(
        self, type_name: str, args: dict[str, Any], *, actor: str = "", now: str = ""
    ) -> ParsedEntity:
        entity_type = self._catalog.get(type_name)
        lock = self._locks.setdefault(f"{self._ws}:{type_name}", asyncio.Lock())
        async with lock:
            number = await allocate(self._fs, self._ws, entity_type.records_path)
            text = render_skeleton(entity_type.skeleton, args, number=number, now=now, actor=actor)
            await self._fs.write(
                self._ws, self._record_path(entity_type.records_path, number), text.encode()
            )
        return parse_entity(text.encode(), number, type_name, entity_type.schema)

    async def get(self, type_name: str, number: int) -> ParsedEntity:
        entity_type = self._catalog.get(type_name)
        raw = await self._fs.read(self._ws, self._record_path(entity_type.records_path, number))
        return parse_entity(raw, number, type_name, entity_type.schema)

    async def update(
        self,
        type_name: str,
        number: int,
        patch: dict[str, Any],
        *,
        expected_version: str | None = None,
    ) -> ParsedEntity:
        """Merge `patch` into the record's fields and write it back. When
        `expected_version` is given (the `version` the caller read), the write is
        rejected with `EntityConflict` if the record changed since — the
        optimistic check (§C6). The read-check-write runs under the per-type lock
        so it's atomic against a racing create/update on this pod (N5).
        `expected_version=None` skips the check (the UI's last-write default)."""
        entity_type = self._catalog.get(type_name)
        path = self._record_path(entity_type.records_path, number)
        lock = self._locks.setdefault(f"{self._ws}:{type_name}", asyncio.Lock())
        async with lock:
            current = parse_entity(
                await self._fs.read(self._ws, path), number, type_name, entity_type.schema
            )
            if expected_version is not None and expected_version != current.version:
                raise EntityConflict(
                    f"{type_name} #{number} changed since you read it "
                    f"(expected {expected_version}, now {current.version})"
                )
            text = serialize_entity({**current.fields, **patch}, current.body)
            await self._fs.write(self._ws, path, text.encode())
        return parse_entity(text.encode(), number, type_name, entity_type.schema)

    async def _parse_type(self, type_name: str) -> list[ParsedEntity]:
        entity_type = self._catalog.get(type_name)
        paths = await self._fs.ls(self._ws, prefix=f"/{entity_type.records_path}/")
        numbered = sorted((n, p) for p in paths if (n := _record_number(p)) is not None)
        return [
            parse_entity(await self._fs.read(self._ws, path), number, type_name, entity_type.schema)
            for number, path in numbered
        ]

    async def _corpus(self) -> Corpus:
        """Every type's clean records, keyed type → number → entity — the input
        for compute-on-read relational projection (§A4)."""
        corpus: Corpus = {}
        for name in self._catalog.names():
            corpus[name] = {e.number: e for e in await self._parse_type(name) if e.ok}
        return corpus

    async def query(self, type_name: str) -> QueryResult:
        entity_type = self._catalog.get(type_name)
        parsed = await self._parse_type(type_name)
        ok = [e for e in parsed if e.ok]
        if any(f.role in (Role.BACKREF, Role.ROLLUP) for f in entity_type.schema.fields):
            corpus = await self._corpus()
            for entity in ok:
                entity.fields.update(compute_derived(entity, entity_type.schema, corpus))
        return QueryResult(entities=ok, invalid=[e for e in parsed if not e.ok])
