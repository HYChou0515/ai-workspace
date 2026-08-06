"""Bounded reads over a whole graph table.

`list_resources(QB.all())` asks the store for the entire table in ONE request,
and specstar's postgres resource store turns that into a single statement
carrying one row-constructor per key. The SQL grows with the corpus until the
database refuses it: measured on Postgres 16, 40k rows is a 937 KB statement
answered with `stack depth limit exceeded` (#689). The weekly pass then stops
running at all.

Every corpus-wide walk in the graph goes through here, so the page size is one
number in one place rather than a constant re-declared beside each caller.
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
from specstar import QB

#: Rows fetched per request while walking a whole table. Bounds the SIZE OF THE
#: REQUEST, not the amount of data — the reconcile genuinely needs every row,
#: because it groups across the whole corpus.
PAGE = 500


def walk_rows(rm, cond=None):
    """Every row matching ``cond``, fetched a page at a time.

    ``list_resources(QB.all())`` asks the store for the whole table in ONE
    request, and specstar's postgres resource store turns that into a single
    statement carrying one row-constructor per key. The SQL therefore grows with
    the corpus until the database refuses it: measured on Postgres 16, 40k rows
    is a 937 KB statement answered with ``stack depth limit exceeded``. The
    weekly reconcile then stops running entirely, and — before the logging this
    ships beside — said nothing about why.

    Paging cannot be left to the caller's own ``limit``: specstar's default is a
    sentinel meaning "no limit", so a query without one asks for everything by
    construction rather than by accident.
    """
    base = QB.all() if cond is None else cond
    offset = 0
    while True:
        page = rm.list_resources(base.limit(PAGE).offset(offset).build())
        if not page:
            return
        yield from page
        if len(page) < PAGE:
            return
        offset += PAGE


def indexed_rows(rm, fields: tuple[str, ...], cond=None) -> Iterator[tuple[str, dict]]:
    """``(resource_id, values)`` for every matching row, read from the INDEX.

    A listing materialises each row through the resource store; a metadata search
    does not. Every field the graph passes read is indexed precisely so these
    walks never have to — over a whole corpus that is the difference between one
    decode per row and none.

    Rows written before a field was indexed do not carry it — specstar extracts
    ``indexed_data`` at write time and does not backfill — and reading an absent
    cell would quietly yield an empty name, a zero count, or an identity that
    looks like it no longer exists. So those rows are collected and read from
    their blobs instead, in bounded pages. That list is empty once the model's
    migrate route has run, which is what makes the migrate a speed-up rather
    than a correctness gate: no deploy has to be ordered against it.

    It lives here rather than beside its first caller because every read on this
    path has to obey the same rule, and the one that did not was the one that
    DELETED on a missing cell rather than skipping.
    """
    stale: list[str] = []
    for meta in rm.iter_all((QB.all() if cond is None else cond).build(), batch_size=PAGE):
        indexed = meta.indexed_data or {}
        if any(field not in indexed for field in fields):
            stale.append(meta.resource_id)
            continue
        yield meta.resource_id, indexed
    for start in range(0, len(stale), PAGE):
        for r in rm.list_resources((QB.resource_id() << stale[start : start + PAGE]).build()):
            yield r.info.resource_id, msgspec.structs.asdict(r.data)
