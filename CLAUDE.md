# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

Respond to the user in **Traditional Taiwanese Chinese (繁體中文 / 台灣用語)**. Keep code, identifiers, commit messages, and file contents in English unless the user explicitly asks otherwise.

## Workflow

- **New feature requests and bug reports**: start with **`/grill-me`** to stress-test the plan and resolve open questions before any code is written.
- **Implementation**: once the plan is clear, use **`/tdd`** to drive the work through the red-green-refactor loop rather than writing implementation first.

## Commands

Backend (Python 3.12, uv-managed):

- Install: `uv sync`
- Run all tests + coverage: `uv run coverage run -m pytest && uv run coverage report`
  - Use `coverage.py` directly — **do not** add `pytest-cov`.
- Run a single test: `uv run pytest tests/path/to/test_file.py::test_name`
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
                          │     - DockerSandbox: one container per sandbox
                          │
                          ├─► FileStore Protocol (filestore/protocol.py)
                          │     - SpecstarFileStore: per-workspace blob inside specstar
                          │
                          ├─► KB chatbot (kb/ + api/kb_routes.py, api/kb_chat_routes.py)
                          │     - Ingestor: bytes → SourceDoc (status=indexing) → chunk + embed
                          │       → DocChunk (slow step runs in a BackgroundTask)
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
- **SSE event schema** (`api/events.py`) is mirrored in `web/src/events.ts`. Keep them in sync when adding event types. The KB chat streams the same events, and the FE renders both chats with the shared `web/src/components/AgentEntryView.tsx` (reasoning / tool cards / metrics).
- **KB ingestion is async**: the upload endpoint calls `Ingestor.store` (fast — creates the `SourceDoc` as `status="indexing"`) and schedules `Ingestor.index` (chunk + embed) on a FastAPI `BackgroundTask`; the doc flips to `ready` / `error`. Citations are NOT in the SSE stream — the FE refetches the thread on `done` to get the persisted `[n]` → `Citation`.
- **Embeddings are computed by us and stored raw** on `DocChunk` (`Vector`, cosine). `KB_EMBED_DIM` must match the embedder's output width; changing it requires re-indexing.
- **specstar singleton vs instance**: always construct a fresh `SpecStar()` instance — never use the module-level `specstar.spec` singleton. This keeps tests isolated.
- **`dict[str, Any]`** (not `dict[str, object]`) for specstar struct fields — `object` breaks JSON-schema generation. Narrow `resource.data` with `assert isinstance(...)` for `ty` (coverage-clean).

See the rationale and rejected alternatives in the conversation history under `/grill-me` (Q1-Q12).
