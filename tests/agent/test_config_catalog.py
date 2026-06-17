"""AgentConfigCatalog — the deploy's KB-facing agent configs keyed by
purpose, plus the preset registry.

#89 P8: the old `workspace_chat` picker / `get` / `default` / per-investigation
`resolve()` were removed — per-App workspace agents resolve through
`apps.catalog.AppCatalog`. What remains is the purpose-keyed accessors the KB
subsystem uses.
"""

from __future__ import annotations

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.resources import AgentConfig


def _kb_pair() -> list[AgentConfig]:
    return [AgentConfig(name="kb-a", model="m1"), AgentConfig(name="kb-b", model="m2")]


def test_configs_for_returns_purpose_entries_in_order():
    cat = AgentConfigCatalog(by_purpose={"kb_chat": _kb_pair()})
    assert [c.name for c in cat.configs_for("kb_chat")] == ["kb-a", "kb-b"]
    assert cat.configs_for("absent") == []


def test_configs_for_returns_a_copy_so_callers_cant_mutate_state():
    cat = AgentConfigCatalog(by_purpose={"kb_chat": _kb_pair()})
    cat.configs_for("kb_chat").clear()
    assert len(cat.configs_for("kb_chat")) == 2


def test_default_for_is_first_entry_or_none():
    cat = AgentConfigCatalog(by_purpose={"kb_chat": _kb_pair()})
    assert cat.default_for("kb_chat").name == "kb-a"  # ty: ignore[unresolved-attribute]
    assert cat.default_for("absent") is None


def test_purposes_lists_only_non_empty_purposes():
    cat = AgentConfigCatalog(by_purpose={"kb_chat": _kb_pair(), "empty": []})
    assert cat.purposes() == ["kb_chat"]


def test_kb_chat_accessors():
    cat = AgentConfigCatalog(kb_chats=_kb_pair())
    assert cat.kb_chat().name == "kb-a"  # first entry  # ty: ignore[unresolved-attribute]
    assert [c.name for c in cat.kb_chats()] == ["kb-a", "kb-b"]
    assert cat.kb_chat_by_name("kb-b").model == "m2"  # ty: ignore[unresolved-attribute]
    assert cat.kb_chat_by_name("nope") is None


def test_single_kb_chat_constructor_arg_seeds_the_purpose():
    cat = AgentConfigCatalog(kb_chat=AgentConfig(name="solo", model="m"))
    assert cat.kb_chat().name == "solo"  # ty: ignore[unresolved-attribute]


def test_infer_modules_accessors():
    cat = AgentConfigCatalog(infer_modules=[AgentConfig(name="im", model="m")])
    assert cat.infer_modules().name == "im"  # ty: ignore[unresolved-attribute]
    assert [c.name for c in cat.infer_modules_configs()] == ["im"]


def test_empty_catalog_accessors_are_safe():
    cat = AgentConfigCatalog()
    assert cat.kb_chats() == []
    assert cat.kb_chat() is None
    assert cat.infer_modules() is None
    assert cat.presets() == {}


def test_presets_returns_a_copy():
    from workspace_app.config.schema import Settings

    presets = Settings().agents.presets
    cat = AgentConfigCatalog(presets=presets)
    got = cat.presets()
    assert got == presets
    got.clear()
    assert cat.presets() == presets  # mutating the copy didn't touch the catalog
