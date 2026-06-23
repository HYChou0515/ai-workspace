"""Characterization tests filling coverage gaps in ``factories.py``.

Covers the pg-only backend branch, the sanity-endpoint skip branches, the
``_agent_endpoint`` empty/missing-preset returns, the App-agent-probe
``cfg is None`` fallback, and the ``_construct_dotted`` error paths — defensive
branches the factory-dispatch suite doesn't reach.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from workspace_app import factories
from workspace_app.config.schema import (
    FilestoreSettings,
    Settings,
)
from workspace_app.factories import (
    _agent_endpoint,
    _app_agent_check_kwargs,
    _backend_for,
    _construct_dotted,
    get_sanity_models,
)

# ─── _backend_for: pg-only connection (factories.py 134->136, 137) ─────────


def test_backend_for_pg_only_builds_a_postgres_connection():
    """`disk_root` empty but `pg_dsn` set: the disk branch is skipped
    (134->136) and a postgres connection is composed (line 137), bound to
    `pg`."""
    s = replace(
        Settings(),
        filestore=replace(FilestoreSettings(), kind="specstar", pg_dsn="postgresql://db/x"),
    )
    cfg = _backend_for(s)
    assert cfg is not None
    assert "pg" in cfg.connections
    assert "local" not in cfg.connections
    assert cfg.connections["pg"].type == "postgres"
    assert cfg.meta.use == "pg"


# ─── _sanity_endpoints skip branches (factories.py 478, 483->475, 487->485) ──


def test_sanity_models_skip_kb_chat_entry_with_unknown_preset():
    """A kb_chat entry pointing at a preset that doesn't exist is skipped
    (line 478 `continue`) — it contributes no model to the sanity matrix."""
    base = Settings()
    s = replace(
        base,
        agents=replace(
            base.agents,
            sub_agents={
                **base.agents.sub_agents,
                "kb_chat": [{"preset": "ghost-preset"}],  # unknown → skipped
            },
        ),
    )
    # The only kb_chat entry is skipped; remaining models come from
    # retrieval_llm / wiki.llm refs.
    models = get_sanity_models(s)
    assert "ghost-preset" not in models  # never resolved to a model


def test_sanity_models_skip_refs_resolving_to_no_model():
    """With `kb.retrieval_llm` and `kb.wiki.llm` both disabled, the ref loop's
    `if model:` is false for each (branch 487->485), so they add nothing."""
    base = Settings()
    s = replace(
        base,
        kb=replace(base.kb, retrieval_llm=None, wiki=replace(base.kb.wiki, llm=None)),
        agents=replace(
            base.agents,
            sub_agents={**base.agents.sub_agents, "kb_chat": []},  # no kb_chat models either
        ),
    )
    assert get_sanity_models(s) == []


def test_sanity_models_skip_kb_chat_entry_with_empty_model():
    """A kb_chat entry whose preset resolves to an empty model name leaves
    `model` falsy, so the `if model:` guard skips it (branch 483->475)."""
    base = Settings()
    presets = dict(base.agents.presets)
    # A preset with an empty model name (allowed at the dataclass level).
    presets["empty-model"] = replace(presets["kb-default"], model="")
    s = replace(
        base,
        kb=replace(base.kb, retrieval_llm=None, wiki=replace(base.kb.wiki, llm=None)),
        agents=replace(
            base.agents,
            presets=presets,
            sub_agents={**base.agents.sub_agents, "kb_chat": [{"preset": "empty-model"}]},
        ),
    )
    # The empty-model entry resolves to "" → skipped; no other model source.
    assert get_sanity_models(s) == []


# ─── _agent_endpoint empty / missing-preset (factories.py 560, 564) ────────


def test_agent_endpoint_with_no_entries_returns_none_triple():
    """A purpose with no usage entries → (None, None, None) (line 560)."""
    base = Settings()
    s = replace(
        base,
        agents=replace(base.agents, sub_agents={**base.agents.sub_agents, "infer_modules": []}),
    )
    assert _agent_endpoint(s, "infer_modules") == (None, None, None)


def test_agent_endpoint_with_unknown_preset_returns_none_triple():
    """A purpose whose first entry names an unknown preset → (None, None, None)
    (line 564)."""
    base = Settings()
    s = replace(
        base,
        agents=replace(
            base.agents,
            sub_agents={**base.agents.sub_agents, "infer_modules": [{"preset": "ghost"}]},
        ),
    )
    assert _agent_endpoint(s, "infer_modules") == (None, None, None)


# ─── _app_agent_check_kwargs cfg-None fallback (factories.py 733) ──────────


def test_app_agent_check_kwargs_returns_skip_when_resolve_yields_none(monkeypatch):
    """Defensive guard (line 733): if the App-catalog resolve ever returns None,
    the probe reports a skip (model None). `AppCatalog.resolve` is typed to
    never return None, so a stub catalog is injected to drive the branch."""

    class _StubCatalog:
        def resolve(self, *, app_slug, profile, attached_preset):
            return None

    class _StubManifest:
        default_profile = "default"

    monkeypatch.setattr(factories, "get_app_catalog", lambda _s: _StubCatalog())
    monkeypatch.setattr(
        "workspace_app.apps.manifest.load_app_manifest", lambda _slug: _StubManifest()
    )
    assert _app_agent_check_kwargs(Settings(), "some-app") == {
        "model": None,
        "base_url": None,
        "api_key": None,
    }


# ─── _construct_dotted error paths (factories.py 782, 792, 796) ────────────


class _NotASanityCheck:
    """A class that is NOT an ISanityCheck subclass — for the type guard."""


def test_construct_dotted_rejects_a_non_dotted_path():
    """An entry with no `.` separator isn't a dotted import path (line 782)."""
    with pytest.raises(ValueError, match="not a dotted import path"):
        _construct_dotted("nodots", object, config_key="health.checks")


def test_construct_dotted_rejects_a_missing_attribute():
    """A real module but a missing class name (line 792)."""
    with pytest.raises(ValueError, match="has no attribute"):
        _construct_dotted("workspace_app.factories.NoSuchClass", object, config_key="health.checks")


def test_construct_dotted_rejects_a_wrong_subclass():
    """A resolved object that isn't a subclass of the expected base raises
    TypeError (line 796)."""
    from workspace_app.health import ISanityCheck

    dotted = f"{__name__}._NotASanityCheck"
    with pytest.raises(TypeError, match="not an ISanityCheck subclass"):
        _construct_dotted(dotted, ISanityCheck, config_key="health.checks")
