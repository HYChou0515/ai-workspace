# Plan: KB retrieval enhancements — operator-tunable, caller-overridable

## Motivation

Currently `Retriever.search()` runs all three enhancements (expand 3-way + HyDE + rerank)
when an LLM is wired. Each call costs 3 LLM round-trips PLUS expand's amplification
(4 query variants × (1 dense + 1 BM25) per variant = 8 ranked-list passes).

- RCA's `kb_search` always runs full enhancement — no caller threads `quick=True` for RCA.
- The existing `quick: bool` is FE-driven and all-or-nothing.
- Operator has zero say in defaults.

User report: 「找超久」.

Goal:
1. Per-knob granularity (`expand`, `hyde`, `rerank` as independent dials)
2. Three-layer override: **LLM tool args > Python caller > operator config**
3. Operator-defined `max` ceiling that LLM-set tool args cannot exceed
4. Shipped default = light enhancement, not full enhancement

## Final design (locked via grilling 2026-06-05)

### Config shape

```yaml
kb:
  retrieval:
    enhancements:
      expand: { default: 1, max: 3 }       # int: 0 = off
      hyde:   { default: 0, max: 1 }       # int: 0 = off
      rerank: { default: true, max: true } # bool: max=false forces off
```

### Knob semantics

| Knob | Type | Meaning | Cost |
|---|---|---|---|
| `expand` | int | Number of alternative query phrasings to generate. `0` = only original query. | 1 LLM call + N × (dense + BM25) per extra variant |
| `hyde` | int | Number of hypothetical documents to embed + retrieve against. `0` = off. | N LLM calls + N × dense per field |
| `rerank` | bool | LLM rerank over the merged candidate set. | 1 LLM call |

### Resolution cascade

For each knob, effective value:

```
raw = tool_args[knob]   if LLM set it
   else caller[knob]    if Python caller set it
   else operator.default

clamped =
  int   → min(max(0, raw), operator.max)
  bool  → raw AND operator.max
```

### Decisions table

| Decision | Choice |
|---|---|
| Granularity | Three independent knobs (not "level" preset) |
| `expand` / `hyde` type | `int` (count of generations, `0` = off) |
| `rerank` type | `bool` |
| Shipped default | `{expand: 1, hyde: 0, rerank: true}` — light, not full |
| Override layers | LLM tool args > Python caller > operator config |
| LLM ceiling | Operator `max` clamps tool-args input |
| `rerank` max | Same shape as int knobs (`{default, max}`) — `max=false` forces off |
| Legacy `quick: bool` (FE composer + context field) | Replace with structured `enhancements` payload |

## Files touched

### Schema (`src/workspace_app/config/schema.py`)

- New `EnhancementInt` dataclass `{default: int, max: int}`
- New `EnhancementBool` dataclass `{default: bool, max: bool}`
- New `EnhancementSettings` dataclass `{expand: EnhancementInt, hyde: EnhancementInt, rerank: EnhancementBool}`
- New `RetrievalSettings` dataclass `{enhancements: EnhancementSettings}` (parallel to `retrieval_llm` ref — one holds "which LLM", the other holds "how aggressive")
- `KbSettings.retrieval: RetrievalSettings = default_factory(...)` — bundled defaults from table above
- Keep `KbSettings.retrieval_llm` intact

### Loader (`src/workspace_app/config/loader.py`)

- Extend `_TOP_SCHEMA["kb"]` with `retrieval: {enhancements: {expand|hyde|rerank: {default, max}}}` nested allow-tree
- `_settings_from_dict`: construct `RetrievalSettings` from merged dict

### Retriever (`src/workspace_app/kb/retriever.py`)

- New `Enhancements` value object `{expand: int | None, hyde: int | None, rerank: bool | None}` — `None` = inherit
- `Retriever.__init__(..., enhancement_defaults: EnhancementSettings | None = None)` — holds operator config
- `Retriever.search(query, collection_ids, on_progress, *, enhancements: Enhancements | None = None)` — replaces `quick: bool`
- Internal: resolution cascade + clamp before each enhancement branch
- `expand_queries(n=...)` already accepts `n`; just thread the resolved value (0 → skip, ≥1 → pass)
- `hypothetical_document` needs to grow `n` param OR loop n times (current returns 1 string; for `n>1` generate n docs)

### Factories (`src/workspace_app/factories.py`)

- `get_retriever(settings, ...)` reads `settings.kb.retrieval.enhancements` and passes to Retriever as `enhancement_defaults`

### Tool (`src/workspace_app/agent/tools.py`)

- `kb_search_impl(ctx, query, expand=None, hyde=None, rerank=None)` — add 3 optional args
- Build `Enhancements(expand=expand, hyde=hyde, rerank=rerank)` and pass to `retriever.search`
- Tool schema (FunctionTool JSON) gains the 3 optional args with brief descriptions

### Context (`src/workspace_app/agent/context.py`)

- Drop `kb_quick: bool` field
- (Operator default lives on the retriever, not the context, so no new context field)

### API (`src/workspace_app/api/kb_chat_routes.py`)

- Replace `body.quick: bool` with `body.enhancements: EnhancementsInput | None`
- Pass-through to retriever via the existing retriever wiring (KB chat builds its own retriever)
- For now, mirror the FE input model in `kb_chat_routes.py`; FE migration deferred (see Out of scope)

### Tests

- `tests/config/test_schema.py` — bundled `RetrievalSettings` defaults; nested types correct
- `tests/config/test_loader.py` — operator config overrides `default`/`max`; unknown nested key raises
- `tests/test_factories.py` — `get_retriever` wires enhancement_defaults
- `tests/kb/test_retriever.py` — resolution cascade (caller > default), clamp (LLM > max → clamped), `expand=0` skips, `hyde=0` skips, `rerank=false` skips
- `tests/agent/test_tools.py` — `kb_search` tool args pass-through; clamping enforced
- `tests/api/test_kb_chat_api.py` — `body.enhancements` wiring

### Docs

- `CONTEXT.md` — add **Enhancements** as domain term (resolution cascade + clamp)
- `configs/config.example.yaml` — new `kb.retrieval` section with comments

## TDD execution order

1. **Schema** — bundled types (`EnhancementInt`, `EnhancementBool`, `EnhancementSettings`, `RetrievalSettings`) + `KbSettings.retrieval`
2. **Loader** — `_TOP_SCHEMA` + `_settings_from_dict`
3. **Retriever** — `Enhancements` value object + `Retriever.search` new signature + resolution cascade
4. **`expand_queries` / `hypothetical_document`** — thread `n` through (HyDE may need new param)
5. **Factories** — `get_retriever` reads from settings
6. **Tool** — `kb_search` adds 3 optional args
7. **Context** — drop `kb_quick`
8. **KB chat routes** — `body.enhancements`
9. **CONTEXT.md** + `configs/config.example.yaml`
10. **Full BE + ruff + ty**

## Out of scope (future)

- FE picker — keep current "Quick" toggle as-is in the FE for now (or map it to `{expand: 0, hyde: 0, rerank: false}` at the route layer); rich enhancement UI is a separate task
- New enhancement types (semantic reranker, etc.)
- Per-collection enhancement overrides — global only
- Per-call cost telemetry (would help operators tune `max`)
