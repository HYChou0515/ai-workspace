"""Register every App's resource on a SpecStar (#89).

Apps are **discovered by scanning** ``apps/`` for dirs that ship an ``app.json``
(the App marker) + a ``model.py`` exposing ``MODEL`` (a ``WorkItemBase``
subclass) + ``INDEXED_FIELDS``. Dropping a new ``apps/<slug>/`` registers it —
no edit here. The App dir name is the slug, so it must be a valid Python package
name (e.g. ``rca``). See ``apps/_template/`` + ``docs/adding-an-app.md``.
"""

from __future__ import annotations

import re
from functools import cache
from importlib import import_module

from specstar import SpecStar

from .base import IndexedFields, WorkItemBase


@cache
def _app_models() -> dict[str, tuple[type[WorkItemBase], IndexedFields]]:
    """slug → (MODEL, INDEXED_FIELDS), by importing each discovered App's
    ``model.py``. Cached — the App set is fixed for a process lifetime."""
    from .catalog import discover_app_slugs

    out: dict[str, tuple[type[WorkItemBase], IndexedFields]] = {}
    for slug in discover_app_slugs():
        mod = import_module(f"{__package__}.{slug}.model")
        model = mod.MODEL
        assert isinstance(model, type) and issubclass(model, WorkItemBase), (
            f"apps/{slug}/model.py: MODEL must be a WorkItemBase subclass"
        )
        out[slug] = (model, mod.INDEXED_FIELDS)
    return out


def app_model(slug: str) -> type[WorkItemBase]:
    """The WorkItem Struct registered for ``slug``. Raises ``KeyError`` for an
    unknown slug (callers 404)."""
    return _app_models()[slug][0]


def registered_apps() -> dict[str, type[WorkItemBase]]:
    """slug → WorkItem Struct for every registered App (copy)."""
    return {slug: model for slug, (model, _idx) in _app_models().items()}


def resource_route(slug: str) -> str:
    """The specstar CRUD route for this App's WorkItem — the FE lists/gets items
    from it. specstar kebab-cases the model name (``RcaInvestigation`` →
    ``/rca-investigation``)."""
    return "/" + re.sub(r"(?<!^)(?=[A-Z])", "-", app_model(slug).__name__).lower()


def register_apps(spec: SpecStar) -> None:
    """`add_model` every discovered App's WorkItem resource with its indexes."""
    for model, indexed_fields in _app_models().values():
        spec.add_model(model, indexed_fields=indexed_fields)
