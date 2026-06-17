"""Register every App's resource on a SpecStar (#89).

Apps are **discovered by scanning** ``apps/`` for dirs that ship an ``app.json``
(the App marker) + a ``model.py`` exposing ``MODEL`` (a ``WorkItemBase``
subclass) + ``INDEXED_FIELDS``. Dropping a new ``apps/<slug>/`` registers it —
no edit here. The App dir name is the slug; a valid Python package name (e.g.
``rca``) is imported normally, while a hyphenated one (e.g. ``topic-hub``) has its
``model.py`` loaded **by file path** (``_load_model_module``). See ``apps/_template/``
+ ``docs/adding-an-app.md``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from functools import cache
from importlib import import_module, resources

from specstar import SpecStar

from .base import IndexedFields, WorkItemBase


def _load_model_module(slug: str):
    """Import a discovered App's ``model.py``.

    Most slugs are valid Python package names → ``import_module`` (and their model
    may use relative imports). A slug with a hyphen (e.g. ``topic-hub``) is **not**
    an importable package, so its ``model.py`` is exec'd **by file path** — the same
    mechanism as a workflow ``run.py`` (``workflow/discovery.py``). Such a model must
    use **absolute imports** (it has no package context)."""
    if slug.isidentifier():
        return import_module(f"{__package__}.{slug}.model")
    model_path = resources.files(__package__) / slug / "model.py"
    mod_name = "_app_" + re.sub(r"\W", "_", slug) + "_model"
    with resources.as_file(model_path) as p:
        spec = importlib.util.spec_from_file_location(mod_name, p)
        assert spec is not None and spec.loader is not None  # a real file yields a loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod  # so msgspec can resolve the Struct's annotations
        spec.loader.exec_module(mod)
    return mod


@cache
def _app_models() -> dict[str, tuple[type[WorkItemBase], IndexedFields]]:
    """slug → (MODEL, INDEXED_FIELDS), by importing each discovered App's
    ``model.py``. Cached — the App set is fixed for a process lifetime."""
    from .catalog import discover_app_slugs

    out: dict[str, tuple[type[WorkItemBase], IndexedFields]] = {}
    for slug in discover_app_slugs():
        mod = _load_model_module(slug)
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
