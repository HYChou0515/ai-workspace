"""Compute-on-read relational projection (#419 §A4).

`backref` collects the records whose `ref` points here; `rollup` aggregates a
field over that backref (closed aggs + an optional single-equality `where`).
Both are computed from the in-memory `corpus` at render time — no stored,
indexed derived state. Ref-path traversal for view columns (`milestone.title`)
is a view concern resolved by the renderer, not here.
"""

from __future__ import annotations

from typing import Any

from .parser import ParsedEntity
from .schema import EntitySchema, Role

Corpus = dict[str, dict[int, ParsedEntity]]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _backref_numbers(from_: str, target_number: int, corpus: Corpus) -> list[int]:
    if "." not in from_:
        return []
    src_type, src_field = from_.split(".", 1)
    records = corpus.get(src_type, {})
    return sorted(
        number
        for number, entity in records.items()
        if _as_int(entity.fields.get(src_field)) == target_number
    )


def _rollup(spec, schema: EntitySchema, target_number: int, corpus: Corpus) -> Any:
    over = schema.field(spec.over) if spec.over else None
    if over is None or over.role is not Role.BACKREF or not over.from_:
        return None
    src_type, src_field = over.from_.split(".", 1)
    matched = [
        entity
        for entity in corpus.get(src_type, {}).values()
        if _as_int(entity.fields.get(src_field)) == target_number
    ]
    if spec.where:
        matched = [
            e for e in matched if all(str(e.fields.get(k)) == v for k, v in spec.where.items())
        ]
    if spec.agg == "count":
        return len(matched)
    values = [n for e in matched if (n := _as_number(e.fields.get(spec.field))) is not None]
    if not values:
        return 0 if spec.agg == "sum" else None
    if spec.agg == "sum":
        return sum(values)
    if spec.agg == "avg":
        return sum(values) / len(values)
    if spec.agg == "min":
        return min(values)
    if spec.agg == "max":
        return max(values)
    return None


def compute_derived(entity: ParsedEntity, schema: EntitySchema, corpus: Corpus) -> dict[str, Any]:
    """The backref + rollup field values for `entity`, computed from `corpus`."""
    out: dict[str, Any] = {}
    for spec in schema.fields:
        if spec.role is Role.BACKREF and spec.from_:
            out[spec.name] = _backref_numbers(spec.from_, entity.number, corpus)
        elif spec.role is Role.ROLLUP:
            out[spec.name] = _rollup(spec, schema, entity.number, corpus)
    return out
