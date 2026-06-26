# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

Respond to the user in **Traditional Taiwanese Chinese (繁體中文 / 台灣用語)**. Keep code, identifiers, commit messages, and file contents in English unless the user explicitly asks otherwise.

## Workflow

- **New feature requests and bug reports**: start with **`/grill-me`** to stress-test the plan and resolve open questions before any code is written.
- **Implementation**: once the plan is clear, use **`/tdd`** to drive the work through the red-green-refactor loop rather than writing implementation first.
- **Phase numbering**: plans use a **flat integer sequence** — `Phase 1`, `Phase 2`, … (`P1`, `P2`, …). **Never** sub-phases like `Phase 1a` / `Phase 1b`. "Phase 1" means Phase 1 is to be *completed*; if work is split off, it becomes the next integer (`Phase 2`), not a letter suffix.

## Commands

Backend (Python 3.12, uv-managed):

- Install: `uv sync --all-extras` (the `process-sandbox` extra — pandera / scipy / scikit-learn / seaborn — is needed by the tabular parser + data-analysis tools; a bare `uv sync` leaves `test_infer_modules` failing)
- Run all tests + coverage (the **authoritative 100% gate** — full suite, needs docker / uv / etc. available locally): `uv run coverage run -m pytest && uv run coverage combine && uv run coverage report --fail-under=100`
  - Use `coverage.py` directly — **do not** add `pytest-cov`.
  - `coverage` runs in **`parallel` mode** (so CI's xdist workers each record their own data), which means a `coverage combine` step is now **required** before `report` — even for a serial local run.
  - **CI runs UNIT tests only** — `pytest -m "not integration" -n auto` (parallel), ~97% coverage, **not** gated at 100%. **integration** tests (real docker / subprocess sandbox / jupyter kernel / uv / ollama; tagged `@pytest.mark.integration`) fill a CI runner's disk and take ~90 min, so they only run in the **full local suite** above — which is where the 100% gate lives.
- Run a single test: `uv run pytest tests/path/to/test_file.py::test_name`
- Run the fast unit subset locally (what CI runs): `COVERAGE_PROCESS_START=pyproject.toml PYTHONPATH=. uv run pytest -m "not integration" -n auto && uv run coverage combine && uv run coverage report`
- Lint + format: `uv run ruff check && uv run ruff format --check`
- Type check: `uv run ty check`
- Run the app: `uv run python -m workspace_app` (serves API + SPA on 127.0.0.1:8000)

Frontend (React + Vite, lives in `web/`):

- Install: `cd web && pnpm install`
- Dev server with backend proxy: `cd web && pnpm run dev` (5173)
- Build production bundle: `cd web && pnpm run build` (produces `web/dist`, which the backend auto-mounts)
- Type check only: `cd web && pnpm run typecheck`

## Architecture

Pluggable layers connected through Protocols — swap any single piece by writing a new implementation and injecting it into `create_app`.

```
React SPA (web/) ─► FastAPI app (api/) ─► OpenAI Agents SDK (api/litellm_runner.py)
                          │                       │
                          │                       └─► LiteLLM ─► Ollama / hosted LLM
                          │
                          ├─► AgentRunner Protocol (api/runner.py)
                          │     - LitellmAgentRunner: real LLM, see above
                          │     - ScriptedAgentRunner: scripted events, used by tests
                          │
                          ├─► Sandbox Protocol (sandbox/protocol.py)
                          │     - MockSandbox: in-memory, for tests
                          │     - LocalProcessSandbox: subprocess + temp dir, default for VM deploys
                          │
                          ├─► FileStore Protocol (filestore/protocol.py)
                          │     - SpecstarFileStore: per-workspace blob inside specstar
                          │
                          ├─► KB chatbot (kb/ + api/kb_routes.py, api/kb_chat_routes.py)
                          │     - Ingestor: bytes → SourceDoc (status=indexing) → chunk + embed
                          │       → DocChunk (store + slow index both offloaded via asyncio.to_thread)
                          │     - Embedder Protocol (kb/embedder.py): HashEmbedder (tests),
                          │       LitellmEmbedder (Ollama/hosted; raw vectors stored on DocChunk)
                          │     - Chunker Protocol (kb/chunker.py): FixedTokenChunker
                          │     - Retriever (kb/retriever.py): dense (specstar native vector
                          │       query) + BM25 → RRF → MMR → parent-doc merge; optional
                          │       multi-query / HyDE / rerank when an Llm is wired
                          │     - KB agent = the SAME AgentRunner with a KB AgentToolContext
                          │       (retriever + collection_ids, no sandbox) + the kb_search tool;
                          │       the RCA agent reaches it via the ask_knowledge_base tool
                          │
                          └─► specstar (resources/): auto-CRUD for Workspace, AgentConfig,
                                Conversation, and KB Collection / SourceDoc / DocChunk / KbChat
```

Key conventions:

- **Sandbox is created lazily** by the agent's `exec` tool on first use (grill-me Q10 "a2+" policy). Pure file operations go through FileStore and never spin one up.
- **AgentRunner Protocol** is the swap point between scripted tests and live LLM. Tests use `ScriptedAgentRunner`; production uses `LitellmAgentRunner`. The **KB agent reuses the same runner** — `AgentToolContext` serves both flavours (RCA: sandbox/filestore/sync + file/exec tools; KB: retriever + collection_ids + `kb_search`, no sandbox), so RCA-only fields are optional.
- **Chat turns run through one `ChatTurnEngine`** (`api/turns.py`) shared by RCA workspace + KB chat: per-conversation lock, one cancellable in-flight turn (new message cancels the prior one), the `_drive` pump (CancelledError → `RunCancelled`, other → `RunError`), the SSE `gen()` that reduces events into neutral `TurnMessage`s, and `cancel()`/`forget()`. Each surface only builds its `AgentToolContext` and passes an `on_complete` that maps `TurnMessage` → its model (`Message` / `KbMessage`). `InvestigationRegistry` owns only the **sandbox** lifecycle (RCA). Don't reimplement turn/cancel/SSE per surface — extend the engine.
- **SSE event schema** (`api/events.py`) is mirrored in `web/src/events.ts`. Keep them in sync when adding event types. The KB chat streams the same events, and the FE renders both chats with the shared `web/src/components/AgentEntryView.tsx` (reasoning / tool cards / metrics).
- **`ask_knowledge_base`** (RCA→KB bridge, non-streaming `answer_question`) relays the KB sub-agent's searches + reasoning into the RCA stream via `ctx.on_exec_output` (ToolLog) — `answer_question(on_event=…)` + `kb_progress`, so the RCA turn shows KB progress live instead of stalling.
- **SourceDoc id is an opaque, slash-free token** (`kb/doc_id.encode_doc_id` = the natural key `{collection}/{user}/{path}` percent-encoded; specstar ids can't hold `/`). **Never parse it** — read `path`/`collection`/`user` from the record + `created_by` meta. `render_document` takes it as a query param (`GET /kb/documents?id=`); the FE treats it opaquely.
- **No-config fallback**: when an investigation has no attached `AgentConfig`, the turn runs with the **first** config in the store (earliest created), not a bare default — `_resolve_agent_config`.
- **KB ingestion is async + off the loop**: the upload endpoint runs `Ingestor.store` (fast — `SourceDoc` as `status="indexing"`) and schedules `Ingestor.index` (chunk + embed), **both via `asyncio.to_thread`** so the blocking magic-sniff / specstar I/O / embedding HTTP never sits on the event loop; the doc flips to `ready` / `error`. Citations are NOT in the SSE stream — the FE refetches the thread on `done` to get the persisted `[n]` → `Citation`.
- **Embeddings are computed by us and stored raw** on `DocChunk` (`Vector`, cosine). `KB_EMBED_DIM` must match the embedder's output width; changing it requires re-indexing.
- **specstar singleton vs instance**: always construct a fresh `SpecStar()` instance — never use the module-level `specstar.spec` singleton. This keeps tests isolated.
- **`dict[str, Any]`** (not `dict[str, object]`) for specstar struct fields — `object` breaks JSON-schema generation. Narrow `resource.data` with `assert isinstance(...)` for `ty` (coverage-clean).
- **Backfilling a new index onto existing rows**: specstar extracts `indexed_data` at write time and does NOT auto-backfill — rows written before the index group under `None` and under-count in aggregates. To make them countable, register the model as `Schema("vN").step(None, _reindex_only, source_type=Model)` (the `None` step covers rows written before any `Schema`), then an operator runs the **migrate route** `POST /{model}/migrate/execute` (specstar's `MigrateRouteTemplate` — opt-in, registered globally in `make_spec`) to re-extract their `indexed_data`. `rm.migrate` is the only backfill op; **don't hand-roll a reindex loop** (specstar discussions #365/#366).
- **List/page aggregates must be scoped**: count/sum for a page goes through `exp_aggregate_by(..., query=...)` bounded to the page's ids (or the collection) — never a global group-by just to look up one page (e.g. `doc_cited_for_ids(spec, ids)`, not `doc_cited(spec)`, in `list_documents`). `.contains` is a membership *filter*, not a group-by (it can't produce per-element counts). On an **indexed `list[str]`** field it is EXACT element membership on every backend **as of specstar 0.11.9** — Postgres `@>` / SQLite `json_each`, so `"m4"` does NOT match a card keyed `"m40"` (specstar #378/#362; our #181). The precondition can silently regress: the field must stay in `indexed_fields` AND annotated `list[...]` so specstar registers it as a list field, else SQL `.contains` falls back to substring `LIKE` (and unit tests run on the in-memory backend, which always did element membership, so they won't catch a Postgres-only regression).
- **FE data layer is TanStack Query**: GET-style reads go through `useQuery` (keys in `web/src/api/queryKeys.ts`, one client in `web/src/api/queryClient.ts`); writes are `useMutation` + `invalidateQueries`. SSE stays imperative (`useAgent`/`useKbChat`), but their initial hydration is a `useQuery`. Components/hooks under test need a provider — wrap with `web/src/test/queryWrapper.tsx` (`QueryWrap` / `renderWithQuery`). The signed-in user id is `api.getCurrentUser()` via `useCurrentUser()` (mocked until SSO), not a hardcoded constant.

See the rationale and rejected alternatives in the conversation history under `/grill-me` (Q1-Q12).
