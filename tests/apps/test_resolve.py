from workspace_app.apps.catalog import AppCatalog
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import resolve_item_agent_config
from workspace_app.config.schema import Settings
from workspace_app.resources import make_spec


def _app_catalog() -> AppCatalog:
    return AppCatalog(presets=Settings().agents.presets)


def test_resolve_new_app_item_uses_the_app_catalog():
    """A new per-App WorkItem resolves its turn's AgentConfig via the 3-layer
    AppCatalog — model from its chosen preset, system prompt from the App."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(RcaInvestigation)
    rid = rm.create(
        RcaInvestigation(title="t", owner="u", attached_preset="claude-opus")
    ).resource_id

    cfg = resolve_item_agent_config(spec, _app_catalog(), rid)
    assert cfg is not None
    assert cfg.model == "claude-opus-4-7"  # the chosen preset
    assert "RCA Agent" in cfg.system_prompt  # the App's base prompt
    assert "rca-tools" in (cfg.allowed_tools or [])  # the App ceiling (default profile)


def test_resolve_composes_the_profile_skill_index_into_the_prompt():
    """#89 T1d: a profile that ships skills (local-lab → report-format) has its
    (name, description) index composed into the resolved system prompt, so the
    agent knows what `read_skill` can load without calling it first."""
    cfg = _app_catalog().resolve(app_slug="rca", profile="local-lab", attached_preset=None)
    assert "## Available skills" in cfg.system_prompt
    assert "report-format" in cfg.system_prompt


def test_resolve_default_profile_advertises_the_apps_shared_skill():
    """#298: the App opts into the shared author-skill (agent.skills), so even the
    default profile — which ships no package skills of its own — advertises it. The
    co-authoring entry point is reachable in every profile."""
    cfg = _app_catalog().resolve(app_slug="rca", profile="default", attached_preset=None)
    assert "## Available skills" in cfg.system_prompt
    assert "author-skill" in cfg.system_prompt


def test_resolve_unknown_item_returns_none():
    """An id that isn't any App's WorkItem (fresh/unknown) resolves to None —
    the turn falls back to the runner's own default."""
    spec = make_spec(default_user="u")
    cfg = resolve_item_agent_config(spec, AppCatalog(presets={}), "absent-id")
    assert cfg is None
