"""#89 — App registration is a *scan* of `apps/`, not a hardcoded list, so
dropping a new `apps/<slug>/` (app.json + model.py) registers it with no edit
to the registry. (Closes the old discovery/registration split where a new app
was discovered by the launcher but its model lookup 500'd.)"""

from __future__ import annotations

from workspace_app.apps.catalog import discover_app_slugs
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.registry import app_model, registered_apps, resource_route
from workspace_app.resources import make_spec


def test_registration_follows_discovery():
    """Every discovered App (a dir with app.json + model.py) is registered —
    the two sets are identical, so the launcher never shows an app whose model
    isn't registered."""
    assert set(registered_apps()) == set(discover_app_slugs())


def test_app_model_and_route_resolve_for_a_discovered_app():
    assert app_model("rca") is RcaInvestigation
    assert resource_route("rca") == "/rca-investigation"


def test_unknown_slug_raises_keyerror():
    import pytest

    with pytest.raises(KeyError):
        app_model("not-an-app")


def test_make_spec_registers_every_discovered_app_model():
    """The scan-driven `register_apps` adds each App's model to the spec — a
    resource manager exists for it."""
    spec = make_spec(default_user="u")
    for slug in discover_app_slugs():
        assert spec.get_resource_manager(app_model(slug)) is not None


def test_a_hyphenated_slug_app_loads_via_file_path():
    """Topic Hub's slug `topic-hub` has a hyphen, so `apps.topic-hub.model`
    isn't `import_module`-able. The registry loads `model.py` by file path, so a
    hyphenated-slug App still registers + resolves like any other."""
    from workspace_app.apps.base import WorkItemBase

    model = app_model("topic-hub")
    assert issubclass(model, WorkItemBase)
    assert resource_route("topic-hub").startswith("/")


def test_template_scaffold_is_not_discovered_or_registered():
    """`apps/_template/` is `_`-prefixed → skipped: it must not register or show
    on the launcher (it's the copy-me scaffold, not a real App)."""
    assert "_template" not in discover_app_slugs()
    assert "_template" not in registered_apps()


def test_template_scaffold_stays_a_valid_app():
    """Rot-guard: even though it's not discovered, the scaffold must stay a
    VALID App so copying it works — its manifest loads, its toggles are coherent,
    and its model projects domain fields. (Catches the schema drifting away from
    the example.)"""
    from importlib import import_module

    from workspace_app.apps.base import WorkItemBase
    from workspace_app.apps.catalog import validate_function_coherence
    from workspace_app.apps.manifest import load_app_manifest
    from workspace_app.apps.schema import project_fields

    manifest = load_app_manifest("_template")
    validate_function_coherence(manifest)  # raises if incoherent
    model = import_module("workspace_app.apps._template.model").MODEL
    assert issubclass(model, WorkItemBase)
    assert {"priority", "status"} <= {f.name for f in project_fields(model)}
