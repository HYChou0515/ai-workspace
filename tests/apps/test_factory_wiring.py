from workspace_app.config.schema import Settings
from workspace_app.factories import get_app_catalog


def test_get_app_catalog_builds_from_presets_and_resolves():
    """`get_app_catalog` builds an AppCatalog from the deploy's agents.presets
    (validating every App's coherence at startup) and can resolve a turn."""
    cat = get_app_catalog(Settings())
    cfg = cat.resolve(app_slug="rca", profile="default", attached_preset="qwen3-local")
    assert cfg.model  # supplied by the bundled qwen3-local preset
    assert "exec" in (cfg.allowed_tools or [])
