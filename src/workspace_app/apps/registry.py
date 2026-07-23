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

from ..perm.checker import work_item_permission_event_handler
from ..perm.scope import GroupsProvider, work_item_access_scope
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


def register_apps(
    spec: SpecStar,
    superusers: frozenset[str] = frozenset(),
    *,
    groups_provider: GroupsProvider | None = None,
) -> None:
    """`add_model` every discovered App's WorkItem resource with its indexes.

    #306: every WorkItem also gets the shared `Permission` enforcement — the
    `permission.visibility` / `permission.read_meta` indexes the `access_scope`
    filters on (row-level read/list visibility → 404) plus the per-verb write ACL
    (403) via the `event_handlers` slot (the `permission_checker` slot is shadowed;
    see `perm.checker`). Appended to EVERY App's indexes here, so an App's
    `model.py` needs no permission boilerplate. A legacy item written before the
    index has no `permission` → `visibility` is null → treated public (the scope's
    `is_null` branch), so no migration changes visibility.

    #608: `groups_provider` is threaded into `work_item_access_scope` so a
    `group:<id>` grant on an item resolves in the storage-list scope too."""
    perm_indexes: IndexedFields = [
        ("permission.visibility", str),
        ("permission.read_meta", list),
    ]
    for model, indexed_fields in _app_models().values():
        spec.add_model(
            model,
            indexed_fields=[*indexed_fields, *perm_indexes],
            access_scope=work_item_access_scope(superusers, groups_provider),
            event_handlers=[work_item_permission_event_handler(superusers)],
        )
