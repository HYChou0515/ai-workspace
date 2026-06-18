"""Nested Settings tree + bundled defaults.

The shape mirrors the YAML schema agreed in Q3 (top-level sections like
`server` / `sandbox` / `kb` / `agents`) — each section is a frozen
dataclass so downstream consumers get typed access (`settings.kb.embedder.
model`) instead of dict-key navigation.

The default constructor `Settings()` gives the same values an operator
would get from an empty `config.yaml` — i.e. the bundled defaults
(Q7: layered defaults). The loader takes those, layers operator YAML on
top, then walks the merged dict to construct `Settings(...)`. So these
tests pin down: what does a fresh deploy run with when no config.yaml
is supplied?
"""

from __future__ import annotations

from workspace_app.config.schema import (
    AgentsSettings,
    EnhancementBool,
    EnhancementInt,
    EnhancementSettings,
    KbSettings,
    Preset,
    RetrievalLlmRef,
    RetrievalSettings,
    Settings,
)


def test_default_settings_constructs_with_no_args():
    s = Settings()
    assert isinstance(s, Settings)


def test_server_section_defaults_match_the_agreed_schema():
    s = Settings().server
    assert s.default_user == "default-user"
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.root_path == ""


def test_sandbox_section_defaults():
    s = Settings().sandbox
    assert s.kind == "local"
    assert s.root is None
    assert s.exec_timeout == 60.0
    assert s.isolate is None


def test_filestore_section_defaults():
    s = Settings().filestore
    assert s.kind == "memory"
    assert s.pg_dsn == ""
    assert s.disk_root == ""


def test_runner_section_defaults():
    s = Settings().runner
    assert s.max_retries == 2
    assert s.max_turns == 10


def test_llm_section_defaults():
    s = Settings().llm
    assert s.base_url == ""
    assert s.api_key == ""


def test_read_file_section_defaults():
    s = Settings().read_file
    assert s.max_lines == 2000
    assert s.max_chars == 200_000


def test_history_section_defaults():
    s = Settings().history
    assert s.max_messages == 40


def test_kb_section_is_nested_dataclasses():
    """`kb` itself is the deep-nested area; verify each subsection is
    its own typed dataclass and defaults match."""
    kb: KbSettings = Settings().kb
    assert kb.embedder.model == "ollama/bge-m3"
    assert kb.embedder.timeout == 60.0
    assert kb.embedder.num_retries == 2
    assert kb.embedder.batch_size == 64
    assert kb.chunker.max_tokens == 256
    assert kb.chunker.overlap == 32
    # retrieval_llm is now a preset reference (RetrievalLlmRef) — not a
    # flat {model, base_url, api_key} block. See test below for the
    # default reference shape.
    assert kb.code_embedder.model == ""  # disabled by default
    assert kb.git.sync_check_interval_sec == 300


def test_default_kb_retrieval_llm_references_the_bundled_preset():
    """Default `kb.retrieval_llm` is a preset reference pointing at
    the bundled `kb-retrieval` preset. Operator overrides the
    referenced preset (or swaps the ref to point elsewhere) instead
    of editing parallel model/endpoint fields."""
    ref = Settings().kb.retrieval_llm
    assert isinstance(ref, RetrievalLlmRef)
    assert ref.preset == "kb-retrieval"
    # `model` empty on the ref = "inherit from the named preset".
    assert ref.model == ""
    # Same for the per-ref endpoint creds.
    assert ref.llm.base_url == ""
    assert ref.llm.api_key == ""


def test_default_kb_wiki_references_the_bundled_wiki_preset():
    """#56: the wiki LLM follows the same preset-reference pattern as
    `kb.retrieval_llm` / `kb.vlm_llm` (was the odd-one-out flat
    `runner.wiki_model`). `kb.wiki.llm` is a `RetrievalLlmRef` pointing
    at the bundled `wiki-default` preset; the step budgets live alongside
    it instead of under `runner`."""
    wiki = Settings().kb.wiki
    assert isinstance(wiki.llm, RetrievalLlmRef)
    assert wiki.llm.preset == "wiki-default"
    assert wiki.llm.model == ""  # inherit from the named preset
    assert wiki.maintainer_max_turns == 40
    assert wiki.reader_max_turns == 24


def test_kb_wiki_can_be_disabled_via_none():
    """`kb.wiki.llm: null` is the off switch — wiki maintenance/reading
    disabled (mirrors `kb.vlm_llm: null`)."""
    import dataclasses

    s = Settings()
    disabled = dataclasses.replace(s.kb, wiki=dataclasses.replace(s.kb.wiki, llm=None))
    assert disabled.wiki.llm is None


def test_bundled_wiki_default_preset_has_only_model():
    """`wiki-default` is the bundled LLM-only preset `kb.wiki.llm`
    references — no prompt/tools (the wiki agents own those in code; the
    preset supplies only which model + endpoint drives them)."""
    presets = Settings().agents.presets
    assert "wiki-default" in presets
    p = presets["wiki-default"]
    assert isinstance(p, Preset)
    assert p.model == "ollama_chat/qwen3:14b"
    assert p.prompt_file == ""
    assert p.allowed_tools is None


def test_message_queue_section_defaults_to_simple():
    """#58/#59/#82: the background job queue backend (wiki maintenance +
    KB indexing) is config-selectable; the default is the specstar-backed
    `simple` queue (multipod via the shared backend, no extra infra)."""
    mq = Settings().message_queue
    assert mq.kind == "simple"
    assert mq.rabbitmq.url == ""


def test_rabbitmq_settings_expose_production_knobs_with_specstar_defaults():
    """The broker-backed path is tunable for production: prefix (namespacing
    a shared broker), retry policy, and — crucially for slow KB index jobs —
    the heartbeat. Defaults mirror specstar's own so an unset knob is a no-op."""
    rmq = Settings().message_queue.rabbitmq
    assert rmq.queue_prefix == "specstar:"
    assert rmq.max_retries == 3
    assert rmq.retry_delay_seconds == 10
    assert rmq.heartbeat_seconds == 600


def test_runner_section_no_longer_carries_wiki_fields():
    """#56: the wiki knobs moved out of `runner` into `kb.wiki`."""
    r = Settings().runner
    assert not hasattr(r, "wiki_model")
    assert not hasattr(r, "wiki_maintainer_max_turns")


def test_default_retrieval_enhancements_match_shipped_defaults():
    """Bundled defaults are intentionally LIGHT (not full): expand=1
    alt query, no HyDE, rerank on. Operators who want full enhancement
    raise `expand` and/or `hyde` explicitly. The `max` ceiling caps
    what LLM-set tool args can request — bundled max permits up to
    3-way expand and 1 HyDE doc."""
    r = Settings().kb.retrieval
    assert isinstance(r, RetrievalSettings)
    assert isinstance(r.enhancements, EnhancementSettings)
    assert r.enhancements.expand == EnhancementInt(default=1, max=3)
    assert r.enhancements.hyde == EnhancementInt(default=0, max=1)
    assert r.enhancements.rerank == EnhancementBool(default=True, max=True)


def test_enhancement_int_and_bool_are_frozen_dataclasses():
    """Both enhancement value types are frozen so loaded settings
    can't be mutated downstream."""
    import dataclasses

    e_int = EnhancementInt(default=1, max=3)
    e_bool = EnhancementBool(default=True, max=True)
    try:
        e_int.default = 99  # type: ignore[misc]  # ty: ignore[invalid-assignment]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("EnhancementInt must be frozen")
    try:
        e_bool.default = False  # type: ignore[misc]  # ty: ignore[invalid-assignment]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("EnhancementBool must be frozen")


def test_kb_retrieval_llm_can_be_disabled_via_none():
    """Setting retrieval_llm to None is the "disable enhancements"
    path — get_kb_llm returns None, multi-query / HyDE / rerank stay
    off."""
    import dataclasses

    s = Settings()
    disabled = dataclasses.replace(s.kb, retrieval_llm=None)
    assert disabled.retrieval_llm is None


def test_agents_section_has_presets_and_kb_chat():
    a: AgentsSettings = Settings().agents
    assert isinstance(a.presets, dict)
    # kb_chat is the KB chat picker's usage list — KB routes resolve it.
    assert a.kb_chat is not None


def test_bundled_presets_include_qwen3_claude_openai_and_kb_default():
    """The four bundled presets we agreed on (Q5/Q7 plan):
    qwen3-local (Qwen3 14B), claude-opus, openai-mini, kb-default."""
    presets = Settings().agents.presets
    assert set(presets) >= {"qwen3-local", "claude-opus", "openai-mini", "kb-default"}
    assert isinstance(presets["qwen3-local"], Preset)
    assert presets["qwen3-local"].model == "ollama_chat/qwen3:14b"
    assert presets["claude-opus"].model == "claude-opus-4-7"
    assert presets["openai-mini"].model == "openai/gpt-4o-mini"
    assert presets["kb-default"].model == "ollama_chat/qwen3:14b"
    # kb-default ships kb_search so the KB chat works out of the box, plus
    # lookup_glossary (#106) so an unknown term resolves from the glossary
    # before the slow kb_search.
    assert presets["kb-default"].allowed_tools == ["kb_search", "lookup_glossary"]


def test_bundled_kb_retrieval_preset_has_only_model():
    """kb-retrieval is the bundled preset for kb.retrieval_llm. It
    needs no prompt/tools (the retriever just consumes the LLM
    endpoint for multi-query / HyDE / rerank), so prompt_file stays
    empty and allowed_tools is None."""
    presets = Settings().agents.presets
    assert "kb-retrieval" in presets
    p = presets["kb-retrieval"]
    assert isinstance(p, Preset)
    assert p.model == "ollama_chat/qwen3:14b"
    assert p.prompt_file == ""
    assert p.allowed_tools is None


def test_preset_prompt_file_is_optional_for_minimal_recipes():
    """Preset.prompt_file defaults to empty so an operator can declare
    a minimal LLM recipe (e.g. for kb.retrieval_llm) without a prompt."""
    p = Preset(model="m")
    assert p.prompt_file == ""


def test_agents_sub_agents_holds_all_purpose_lists_keyed_by_name():
    """B-flat schema: every `agents.<purpose>` usage list (except the
    reserved `presets`) lands in `AgentsSettings.sub_agents` keyed by
    purpose name. Operators add new sub-agent purposes by writing
    `agents.<new_name>: [...]` — no schema field needs editing."""
    a = Settings().agents
    assert isinstance(a.sub_agents, dict)
    assert set(a.sub_agents) >= {"kb_chat", "infer_modules"}
    # Each entry is a usage list (list of dicts).
    assert a.sub_agents["kb_chat"][0]["preset"] == "kb-default"
    assert a.sub_agents["infer_modules"][0]["preset"] == "infer-modules-default"


def test_agents_legacy_named_accessors_still_work():
    """Back-compat: `agents.kb_chat` / `infer_modules` keep working as named
    accessors over `sub_agents`."""
    a = Settings().agents
    assert a.kb_chat[0]["preset"] == "kb-default"
    assert a.infer_modules[0]["preset"] == "infer-modules-default"


def test_bundled_kb_chat_ships_multiple_entries_so_the_picker_shows_choices():
    """Issue #32 follow-up: the FE picker is now always rendered when
    at least one kb_chat entry exists, but a single-entry dropdown
    still doesn't show a *choice*. Ship more than one bundled entry
    (mirrors workspace_chat's 3-entry default) so the operator sees
    real options the first time the chat opens, and learns the
    feature without reading docs."""
    kb_chat = Settings().agents.kb_chat
    assert isinstance(kb_chat, list)
    assert len(kb_chat) >= 2
    # The local-only entry is the default (no creds needed) — first in
    # the list so a fresh deploy works without setting up hosted keys.
    assert kb_chat[0]["preset"] == "kb-default"
    # At least one other bundled entry exists; its preset is a real
    # entry in `agents.presets`.
    presets = Settings().agents.presets
    for entry in kb_chat:
        assert entry["preset"] in presets


def test_settings_is_frozen_so_top_level_reassignment_fails():
    """frozen=True prevents accidental mutation of the loaded
    settings (the loader builds once, downstream reads only)."""
    import dataclasses

    s = Settings()
    try:
        s.server = None  # type: ignore[misc]  # ty: ignore[invalid-assignment]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Settings should be frozen")
