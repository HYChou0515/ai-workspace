# Plan: 3-phase follow-ups (architecture + tech debt + FE)

## Overview

Three follow-ups from the architecture review + recent retrieval-enhancements work.
Sequencing **A → B → C**.

- **Phase A — Fix the 3 chunk-indexing test failures.** They're real bugs (verified
  via `git stash` twice); skipping them is technical debt accruing interest.
- **Phase B — Unify the sub-agent usage-list pattern.** Architecture candidates
  1 + 4 from the 2026-06-05 review. Decisions locked via grilling 2026-06-05.
- **Phase C — Build the FE enhancement picker.** Surfaces the `expand` / `hyde` /
  `rerank` knobs added in commit `7ea0f03` so operators can dial per-message.

A goes first because: (1) it's smallest scope; (2) bugs accrue interest; (3)
investigating might surface state that affects B (B touches `_ask_kb` /
`_infer_modules` which sit close to the KB indexing path).

B and C are independent of each other after A.

---

## Phase A — Fix the 3 pre-existing chunk-indexing test failures

### Symptom

Three tests fail on `master`, verified pre-existing via `git stash` (commits
`c00db12` and earlier). All assert `chunks > 0` after upload / re-index but
see `chunks == 0`:

- `tests/api/test_cited_surface.py::test_cited_counts_surface_on_collections_documents_and_chunks`
- `tests/api/test_kb_api.py::test_reindex_collection_rebuilds_all_docs`
- `tests/api/test_kb_api.py::test_reindex_single_document`

I have **deselected** them in two consecutive commits (`cb7c0c2` and `7ea0f03`).
The deselects have to go.

### Approach (investigate + fix, one shot)

Run mode locked: **investigate root cause, write minimal regression that pins
the failure, fix it, re-enable the 3 tests, full BE green without any
deselect / ignore flag**. Don't stop midway for confirmation unless root
cause turns out to be huge.

1. **Reproduce** all 3 fails locally without any flags
2. **Bisect / trace** — likely suspects:
   - Recent commit `d530644 faster count chunks of docs` (touched the chunk
     count surface in `kb_routes.py`) — likely culprit, the `defaultdict`
     conversion may have swallowed the count
   - Async race: `Ingestor.index` runs via `asyncio.to_thread`; TestClient
     drains background tasks, but ordering may have shifted
   - The `cited_counts` query path: live count vs cached
3. **Narrow** — write a minimal pytest that reproduces in <5 lines, so the
   fix loop is tight
4. **Fix** the root cause (NOT a sleep, NOT a deselect — actual cause)
5. **Un-deselect** the 3 tests
6. **Verify** — run the 3 tests 10× consecutively to rule out flake
7. **Commit** with the root cause in the message

### Files likely touched

- `src/workspace_app/api/kb_routes.py` (recent chunk-count optimisation —
  primary suspect)
- `src/workspace_app/kb/ingest.py` (Ingestor.index path)
- `src/workspace_app/kb/cited.py` (collection / doc cited counts)
- The 3 affected test files (un-deselect after fix)

### Acceptance

- All 3 tests pass 10 consecutive runs
- Full BE 100% green **with no deselect / ignore flag**
- Root cause documented in commit message — not "fixed flake", but
  "X happened because Y, fixed by Z"

---

## Phase B — Unify sub-agent usage-list pattern

### Motivation

Three same-shaped lists in `agents.{workspace_chat, kb_chat, infer_modules}`
+ two near-identical bridge functions (`_ask_kb`, `_infer_modules`)
duplicate the "usage entry → AgentConfig → bridge" pattern. Adding the
next sub-agent (e.g. QTime pair selector) touches **7 places**. The seam
is real; the abstraction is missing.

### Locked design (grilled 2026-06-05)

#### Schema shape — B-flat

YAML:

```yaml
agents:
  presets: { ... }                 # reserved key — dict of named recipes
  workspace_chat: [ { preset: ... }, ... ]   # usage list, purpose=workspace_chat
  kb_chat:        [ { preset: ... }, ... ]   # usage list, purpose=kb_chat
  infer_modules:  [ { preset: ... }, ... ]   # usage list, purpose=infer_modules
  qtime_pair_selector: [ ... ]    # NEW sub-agent? just add it. Loader accepts any key.
```

**Validator**: every key under `agents` is either `presets` (reserved name
for the recipes dict) or a **usage list** (any other key — value must be
`list[dict]`, each dict matches usage-entry shape).

Adding a new sub-agent purpose:
- ZERO schema work — operator writes `agents.<new_purpose>: [...]` and it's accepted
- ONE bundled preset (in `_BUNDLED_PRESETS`)
- ONE tool impl (in `agent/tools.py`)
- ZERO loader edits, ZERO catalog edits, ZERO context-field additions

#### Concept — AgentRegistry

ONE registry, three (or more) entries. No "sub-agent vs picker" split —
every entry is a `RegisteredAgent` with a `purpose`. `workspace_chat`
included; it just has no bridge (it's not called by another agent).

```python
@dataclass
class RegisteredAgent:
    purpose: str                       # "workspace_chat" / "kb_chat" / "infer_modules" / ...
    configs: list[AgentConfig]         # resolved from the usage entries

class AgentRegistry:
    def get(purpose: str) -> list[AgentConfig]: ...
    def default(purpose: str) -> AgentConfig: ...
    def purposes() -> list[str]: ...
```

#### Bridge — tool自帶 formatter + `ctx.run_subagent`

```python
# Generic helper on AgentToolContext — ONE for all sub-agents.
async def run_subagent(
    self,
    purpose: str,
    payload: str,
    on_event: OutputSink | None = None,
) -> tuple[str, list[Citation]]:
    ...
```

Each tool impl provides its own `format_payload(typed_args) -> str`. The
helper does the rest (`answer_question` + citation collection).

```python
# Example: infer_modules tool impl
async def infer_modules_impl(ctx, step_names, defect_context=None):
    payload = json.dumps({"step_names": step_names, "defect_context": defect_context})
    answer, citations = await ctx.context.run_subagent("infer_modules", payload, ctx.context.on_exec_output)
    ctx.context.subagent_citations["infer_modules"].append(citations)
    return answer
```

Workspace_chat is in the registry but doesn't have a tool impl that calls
`run_subagent` (it IS the primary agent, called by users not other agents).
`run_subagent("workspace_chat", ...)` is legal but unused; no special
casing needed.

#### Citation pool — dict[purpose, list[list[Citation]]]

Replace `ask_kb_citations` + `infer_modules_citations` with:

```python
@dataclass
class AgentToolContext:
    ...
    subagent_citations: dict[str, list[list[Citation]]] = field(default_factory=dict)
```

`persist()` iterates the dict — one loop, all purposes:

```python
for purpose, pools in ctx.subagent_citations.items():
    for citations in pools:
        # bubble to outer assistant message via shared seen_subagent pool
        ...
```

### Files touched

- `src/workspace_app/config/schema.py` — Drop `AgentsSettings.workspace_chat / kb_chat / infer_modules` typed fields. Replace with `agents.sub_agents` dict (or read direct from raw merged dict). Keep `presets`.
- `src/workspace_app/config/loader.py` — `_check_agents_keys` accepts `presets` + any other key as `list[dict]`. Validate each entry as usage dict.
- `src/workspace_app/config/catalog_build.py` — Walk every non-`presets` key, build `RegisteredAgent` entries.
- `src/workspace_app/agent/config_catalog.py` — Rewrite as `AgentRegistry` exposing `get(purpose)` / `default(purpose)` / `purposes()`. Drop `kb_chats()` / `infer_modules_configs()` / `default()` (sub the latter with `default("workspace_chat")`).
- `src/workspace_app/agent/context.py` — Drop `ask_kb_citations` / `infer_modules_citations`. Add `subagent_citations: dict`. Add `run_subagent` callable field. Drop `ask_kb` / `infer_modules` direct callable fields.
- `src/workspace_app/agent/tools.py` — Rewrite `ask_knowledge_base_impl` and `infer_modules_impl` to use `ctx.context.run_subagent(...)`. Each provides its own `format_payload`.
- `src/workspace_app/api/app.py` — Drop `_ask_kb` and `_infer_modules` closures. Add ONE `_make_run_subagent(registry, retriever, ...)` that returns the unified `run_subagent` callable. Wire to context. Persist iteration walks `subagent_citations` dict.
- `src/workspace_app/api/kb_chat_routes.py` — KB chat picker reads `catalog.get("kb_chat")` instead of `catalog.kb_chats()`.
- All sites currently reading `catalog.kb_chats()` / `catalog.infer_modules_configs()` / `catalog.default()` migrate to `catalog.get(...)` / `catalog.default(purpose)`.
- `tests/config/test_schema.py` — Rewrite agents tests for new wildcard shape
- `tests/config/test_loader.py` — Add: arbitrary new purpose key is accepted; `presets` stays reserved
- `tests/config/test_catalog_build.py` — Rewrite catalog tests for AgentRegistry
- `tests/api/test_messages.py` — Citation bubble tests now walk the dict
- `tests/agent/test_tools.py` — `ctx.run_subagent` interaction
- CONTEXT.md — Replace `AgentConfigCatalog` with `AgentRegistry` + `RegisteredAgent`; add `RunSubAgent` callable

### TDD execution order

Migrate ONE sub-agent at a time. Each step is small, reversible, fully tested.

1. **Schema + loader** for wildcard `agents.<any-name>: list[dict]`
2. **AgentRegistry** type + accessor (`get(purpose)` / `default(purpose)`); legacy `kb_chats()` / etc. stay as thin facades over it
3. **Migrate `infer_modules`** to use AgentRegistry + `ctx.run_subagent` (smallest blast radius — most recent addition)
4. **Migrate `kb_chat`** (route + bridge)
5. **Migrate `workspace_chat`** picker (largest blast radius — RCA picker / FE
   `/agent-configs` route / `create_investigation` auto-attach)
6. **Drop legacy facades** + `ask_kb_citations` / `infer_modules_citations`. Drop `_ask_kb` / `_infer_modules` in app.py.
7. **CONTEXT.md** + commit

### Acceptance

- Adding a dummy "echo" sub-agent purpose via test (no real tool, just registry entry) requires editing ≤ 2 files
- No behaviour regression — every existing tool / route / FE pick works
- BE 100% green, full coverage doesn't slip on touched modules

---

## Phase C — FE enhancement picker

### Motivation

Commit `7ea0f03` added the per-knob retrieval-enhancements BE (operator
default + LLM-overridable + clamped by max). FE still has only the legacy
single "Quick" toggle. Operators can't say "use expand=2 for this question"
from the UI.

### Locked design (grilled 2026-06-05)

#### UI shape — Hybrid (3-level dropdown + Advanced sliders)

Default view in the composer:

```
[訊息輸入 ...]     [Mode: ▼ standard]     [送出]
                       ├─ quick
                       ├─ standard ●
                       └─ thorough
                       
                       [▾ Advanced]
```

Click "Advanced" → expand inline panel:

```
[訊息輸入 ...]     [Mode: ▼ custom*]      [送出]

  Advanced (overrides Mode):
  expand:  ──●─ 1 (max 3)
  hyde:    ●─── 0 (max 1)
  rerank:  ☑ on
  
  * Mode switches to "custom" when any slider differs from a level preset
```

Sliders' max is **NOT** sourced from a new endpoint. The FE passes a
generous number (99) and lets BE clamp. Slider UI uses a sensible hardcoded
upper bound (e.g. 10) for display only.

#### Persistence — per-message

Just like `reasoning_effort` and `agent_name`. The composer **remembers
the last selection** within the chat session (local state), but every
message sends its own `body.enhancements`. No chat-level setting.

When chat is reopened in a fresh tab, falls back to "standard" (operator
default).

#### API contract — `body.enhancements` only

FE sends:

```json
{
  "content": "...",
  "reasoning_effort": "medium",
  "agent_name": "KB · Qwen",
  "enhancements": { "expand": 1, "hyde": 0, "rerank": false }
}
```

Translation table (FE side):

| Mode | `body.enhancements` |
|---|---|
| `quick` | `{expand: 0, hyde: 0, rerank: false}` |
| `standard` | `{expand: null, hyde: null, rerank: null}` (all null → BE uses operator default) |
| `thorough` | `{expand: 99, hyde: 99, rerank: true}` (BE clamps to operator max) |
| `custom` (advanced) | whatever the sliders show, verbatim |

#### Drop `body.quick`

Demo branch, no migration. Remove `quick: bool` field on `_MsgBody` and
the route's `body.quick` → `Enhancements(0,0,false)` mapping. FE clients
sending the old field will get a 422 (`extra fields not permitted` per
pydantic strict mode if enabled — otherwise silently ignored, which is
the current behaviour). Acceptable.

### Files touched

- `web/src/components/KbChatComposer.tsx` (or equivalent) — new picker UI
- `web/src/types/kbChat.ts` — `Enhancements` type
- `web/src/api/kbChat.ts` — wire body.enhancements
- `web/src/test/*` — vitest TDD per memory `feedback_fe_tdd`
- `src/workspace_app/api/kb_chat_routes.py` — drop `body.quick` field + the mapping branch
- `tests/api/test_kb_chat_api.py` — drop legacy `quick` test; structured `enhancements` test stays

### TDD execution order

(Per memory `feedback_fe_tdd` — vitest; FE work uses /tdd discipline)

1. **BE cleanup** — drop `body.quick: bool` (small, no UI dep)
2. **FE Enhancement type** — `web/src/types/kbChat.ts`
3. **Translation table** — pure function `modeToEnhancements(mode, sliders): Enhancements` + tests
4. **Composer UI** — Mode dropdown + Advanced disclosure + sliders, wired to the pure translator
5. **Wire to send** — composer's send path passes the resolved `enhancements` to the message POST
6. **Vitest** — UI interaction (clicking thorough sends right body; sliders override mode; mode label updates to "custom" on slider edit)
7. **Manual verify in dev server** (per CLAUDE.md "For UI or frontend changes, start the dev server and use the feature in a browser")

### Acceptance

- Vitest green on composer interactions
- Dev server manual: send "quick" → BE log shows `Enhancements(0,0,false)`; "thorough" → BE clamps; custom slider value passes through
- TypeScript clean
- BE-side test re-confirms `body.quick` rejected / ignored

---

## Cross-phase notes

- Each phase = its own commit (or commit chain for B's incremental migration)
- Per memory `feedback_no_push` — commit locally on `demo`, do not push
- Per memory `feedback_targeted_tests_then_full` — iterate with the affected
  test files; full BE + 100% coverage gate before each commit
- Per memory `feedback_fe_tdd` — all new web/ code is vitest-driven
- Per memory `feedback_pydantic_response_models` — Phase B may touch the
  catalog endpoints `/agent-configs`; ensure they return typed pydantic models
  consistent with FE types

## Out of scope

- Migrating production configs (none — demo branch)
- Telemetry: per-call cost / timing for enhancement use (separate
  observability project; would help operators tune `max`)
- Cross-collection enhancement overrides (global only stays the policy)
- "Cost preview" UX (estimated LLM-call count next to the Mode dropdown)
- Promoting `on_progress` strings to structured events (would help cost
  preview but separate)
