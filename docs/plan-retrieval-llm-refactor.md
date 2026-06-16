# Plan: `kb.retrieval_llm` → preset reference

## Motivation

`kb.retrieval_llm` is currently a standalone `{model, base_url, api_key}` block.
That re-implements what `agents.presets` already does for agent LLMs:

- An operator who flips everything to OpenAI has to edit `agents.presets.qwen3-local.model`,
  `agents.presets.kb-default.model`, AND `kb.retrieval_llm.model` — three places, same idea.
- Credentials (`${OPENAI_API_KEY}`) repeat across N blocks.
- "Retrieval LLM should match the KB sub-agent LLM" is a config invariant nobody can express,
  so it drifts by hand.

Goal: make `kb.retrieval_llm` a preset reference, so "which LLM" has ONE configuration shape
across agent picker + KB chat + infer_modules + retriever.

## Final design (locked via grilling 2026-06-05)

### YAML shape

```yaml
agents:
  presets:
    kb-retrieval:                          # NEW bundled preset
      model: ollama_chat/qwen3:14b
      # no prompt_file / allowed_tools — retriever doesn't use them

kb:
  retrieval_llm: { preset: kb-retrieval }  # default
  # OR — disable enhancements
  retrieval_llm: null
  # OR — inline override (same syntax as workspace_chat[] / kb_chat[])
  retrieval_llm:
    preset: kb-retrieval
    llm: { api_key: ${OPENAI_API_KEY} }
```

### Decisions

| Decision | Choice | Notes |
|---|---|---|
| Stop enhancements | `kb.retrieval_llm: null` | Field omitted = same effect |
| Old flat-form (`{model, base_url, api_key}`) | Hard break — raise "unknown key" | Matches project's strict-validation philosophy |
| Bundled default | New `kb-retrieval` preset in `_BUNDLED_PRESETS`, default `kb.retrieval_llm: {preset: kb-retrieval}` | Identity separate from `kb-default` (KB sub-agent) so future operator can split them |
| Override semantics | Full usage-entry shape (any preset field can be overridden inline) | Consistent with `workspace_chat[]` / `kb_chat[]` / `infer_modules[]` |
| `preset:` field | Required | Operators who want fully-inline config write a new preset |
| Empty `prompt_file` on `Preset` | Allowed (default `""`) | Lets `kb-retrieval` legally exist without a prompt; agent-style callers still required to set it (catalog build enforces) |

## Files touched

### Schema (`src/workspace_app/config/schema.py`)

- `Preset.prompt_file`: `str` → `str = ""` (optional, defaults to empty)
- `_BUNDLED_PRESETS`: add `"kb-retrieval": {"model": "ollama_chat/qwen3:14b"}`
- `RetrievalLlmSettings` (flat dataclass): **delete**
- Add `RetrievalLlmRef` typed dataclass mirroring usage-entry shape:
  `{preset: str, model: str = "", llm: PresetLlmSettings = ...}` (prompt_file/tools fields excluded — retriever doesn't read them)
- `KbSettings.retrieval_llm`: `RetrievalLlmSettings` → `RetrievalLlmRef | None`,
  default `RetrievalLlmRef(preset="kb-retrieval")`

### Loader (`src/workspace_app/config/loader.py`)

- `_TOP_SCHEMA["kb"]["retrieval_llm"]`: replace dataclass-keys lookup with allowed-key set
  `{"preset", "model", "llm"}` (or sentinel + custom validator if `null` needs special handling)
- Handle `kb.retrieval_llm: null` at merge/build time → keep as `None`
- `_check_preset_references` (or new `_check_retrieval_llm_reference`): assert
  `kb.retrieval_llm.preset` references a known preset
- `_settings_from_dict`: build `RetrievalLlmRef` instead of `RetrievalLlmSettings`
- `_check_preset_required_fields`: drop `prompt_file` from required list (only `model` required)

### Factories (`src/workspace_app/factories.py`)

- `get_kb_llm(settings)` rewrite:
  1. `ref = settings.kb.retrieval_llm`; if `ref is None` → return `None`
  2. Resolve `preset = settings.agents.presets[ref.preset]`
  3. Merge: `model = ref.model or preset.model`; `llm.base_url = ref.llm.base_url or preset.llm.base_url or settings.llm.base_url`; same for `api_key`
  4. Build `LitellmLlm(model, base_url=..., api_key=...)`

### Tests

- `tests/config/test_schema.py`:
  - Update `test_kb_section_is_nested_dataclasses`: drop `kb.retrieval_llm.model` direct assert; add new test for `kb.retrieval_llm.preset == "kb-retrieval"`
  - Add: bundled `kb-retrieval` preset exists with `model: ollama_chat/qwen3:14b`, empty `prompt_file`, `allowed_tools is None`
  - Add: `kb.retrieval_llm` defaults to `RetrievalLlmRef(preset="kb-retrieval")` (not None)
- `tests/config/test_loader.py` (or wherever loader validation tests live):
  - Add: old flat-form `kb.retrieval_llm: {model: ..., api_key: ...}` raises "unknown key model"
  - Add: `kb.retrieval_llm: null` → `settings.kb.retrieval_llm is None`
  - Add: `kb.retrieval_llm: {preset: kb-retrieval, llm: {api_key: secret}}` resolves with override
  - Add: unknown preset reference raises
- `tests/test_factories.py`:
  - Rewrite existing 4 tests: build via `RetrievalLlmRef` + preset, not `RetrievalLlmSettings`
  - Cover: `retrieval_llm is None` → `get_kb_llm` returns `None`
  - Cover: ref overrides win over preset; preset wins over top-level `llm.*`

### Docs

- `CONTEXT.md`: add **Preset** and **usage entry** as domain terms; explain
  `kb.retrieval_llm` is now a single usage entry (not a list)
- `configs/config.example.yaml` lines 79-83: replace flat-form example with new syntax;
  show all three states (default preset, null-disable, inline override)

## TDD execution order

(Tasks #275-#281 already created)

1. **#275** — `Preset.prompt_file` optional + `kb-retrieval` bundled preset (smallest, no consumers)
2. **#276** — `KbSettings.retrieval_llm` new shape (`RetrievalLlmRef | None`)
3. **#277** — Loader validates new shape, rejects old
4. **#278** — `factories.get_kb_llm` reads through preset
5. **#279** — `CONTEXT.md` glossary update
6. **#280** — `configs/config.example.yaml` example update
7. **#281** — Full BE suite + 100% coverage + ruff/ty

## Out of scope (future)

- Candidates 1 / 3 / 4 from architecture review (unify usage-list pattern, rename `kb-default`, collapse bridges) — separate refactors
- FE changes — retrieval LLM has no FE picker (operator-only config)
- Migration tooling — `demo` branch, no production configs to migrate
