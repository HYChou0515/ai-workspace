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
