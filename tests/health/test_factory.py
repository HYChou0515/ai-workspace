"""`factories.get_check_registry(settings)` — the seven bundled probes
wired from settings, minus `health.checks_disabled`, plus custom
`health.checks` dotted paths. Construction must not touch the network
(probes only run when executed)."""

from __future__ import annotations

import dataclasses

import pytest

from workspace_app.apps.catalog import discover_app_slugs
from workspace_app.config.schema import Settings
from workspace_app.factories import get_check_registry
from workspace_app.health import CheckResult, ISanityCheck

# The fixed (non-App) bundled probes, in registration order.
_FIXED_IDS = [
    "embedder-default",
    "embedder-code",
    "insight-extraction",
    "retrieval-expand",
    "vlm-describe",
    "agent-kb-chat",
    "agent-infer-modules",
    "agent-wiki-reader",
    "agent-wiki-maintainer",
]


def _bundled_ids() -> list[str]:
    """The fixed probes plus one ``agent-<slug>`` per discovered App (#89 P8 T5).
    Derived from ``discover_app_slugs()`` — the same source the factory iterates —
    so a drop-in App (rca, playground, …) is reflected automatically instead of
    breaking a hardcoded list."""
    return _FIXED_IDS + [f"agent-{slug}" for slug in discover_app_slugs()]


class HelloCheck(ISanityCheck):
    """Module-level so the dotted-path resolver can import it."""

    check_id = "hello"
    description = "custom check"

    def run(self) -> CheckResult:
        return CheckResult(check_id=self.check_id, status="pass")


def test_default_settings_register_all_bundled_checks():
    reg = get_check_registry(Settings())
    assert [c.check_id for c in reg.checks()] == _bundled_ids()


def test_app_agent_probe_targets_the_resolved_app_default_model():
    """#89 P8 closeout T5: each registered App gets a tool-call probe whose model
    is the one its default profile resolves to via AppCatalog — restoring the
    startup capability check the removed workspace_chat 'agent-workspace' had."""
    reg = get_check_registry(Settings())
    checks = {c.check_id: c for c in reg.checks()}
    assert "agent-rca" in checks
    assert checks["agent-rca"]._model == "ollama_chat/qwen3:14b"


def test_fast_set_is_the_embedder_pair():
    """Startup runs the fast set synchronously (Q2: the <1-minute
    items) — connectivity-grade probes only, never LLM capability
    rounds."""
    reg = get_check_registry(Settings())
    assert [c.check_id for c in reg.checks(fast_only=True)] == [
        "embedder-default",
        "embedder-code",
    ]


# Despite the name/docstring, the embedder-default check actually reaches a
# running embedder (ollama) in practice — it passes locally but times out on a
# CI runner with no LLM, so it belongs to the integration tier.
@pytest.mark.integration
def test_default_settings_skip_paths_without_running_llms():
    """With bundled defaults, the code embedder isn't configured —
    its check must report skip when RUN, and running it must not
    require any network (HashEmbedder default path)."""
    reg = get_check_registry(Settings())
    by_id = {c.check_id: c for c in reg.checks()}
    assert by_id["embedder-code"].run().status == "skip"
    # default embedder is the offline HashEmbedder → pass, no network
    assert by_id["embedder-default"].run().status == "pass"


def test_checks_disabled_filters_and_validates():
    base = Settings()
    s = dataclasses.replace(
        base,
        health=dataclasses.replace(base.health, checks_disabled=["vlm-describe"]),
    )
    ids = [c.check_id for c in get_check_registry(s).checks()]
    assert "vlm-describe" not in ids and len(ids) == len(_bundled_ids()) - 1

    bad = dataclasses.replace(
        base,
        health=dataclasses.replace(base.health, checks_disabled=["no-such-check"]),
    )
    with pytest.raises(ValueError, match="no-such-check"):
        get_check_registry(bad)


def test_custom_dotted_path_checks_append():
    base = Settings()
    s = dataclasses.replace(
        base,
        health=dataclasses.replace(base.health, checks=["tests.health.test_factory.HelloCheck"]),
    )
    ids = [c.check_id for c in get_check_registry(s).checks()]
    assert ids[-1] == "hello"

    bad = dataclasses.replace(
        base,
        health=dataclasses.replace(base.health, checks=["totally.made.up.Nope"]),
    )
    with pytest.raises(ValueError, match="totally.made.up.Nope"):
        get_check_registry(bad)


def test_get_replay_service_respects_disabled_components():
    """`get_replay_service` reuses the caller's KB LLM and only builds a
    describer when `kb.vlm_llm` is configured — with both off, replays
    report unsupported instead of probing a model that isn't wired.
    Construction itself never touches the network."""
    from workspace_app.factories import get_replay_service
    from workspace_app.health import ReplayService, ReplayUnsupported

    base = Settings()
    s = dataclasses.replace(base, kb=dataclasses.replace(base.kb, vlm_llm=None))
    service = get_replay_service(s, kb_llm=None)
    assert isinstance(service, ReplayService)
    # No VLM configured → image replay reports unsupported.
    with pytest.raises(ReplayUnsupported):
        service.replay_doc(path="a.png", mime="image/png", blob=b"png")
    # No KB LLM passed → chat-export replay reports unsupported.
    with pytest.raises(ReplayUnsupported):
        service.replay_doc(path="a.chat.json", mime="application/json", blob=b"{}")
