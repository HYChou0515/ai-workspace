# workspace-app

A team-internal web app for running OpenAI Agents inside per-workspace
sandboxes. Each workspace owns a persistent file store; a sandbox is
created on demand when the agent needs to run a shell command, and is
killed when idle.

It also ships a **knowledge-base (KB) chatbot**: upload in-house documents
(markdown / text, or zip/tar archives, or a whole folder) into named
**collections**, which are chunked, embedded, and stored for hybrid retrieval
(dense vector + BM25 + RRF + MMR + parent-document merge, with optional
multi-query / HyDE / LLM rerank). A KB **agent** answers questions over those
collections with inline `[n]` citations that link back to the source document —
and the RCA workspace agent can consult it through an `ask_knowledge_base` tool.
Reach it from the top-bar **Ask agent** drawer (fast chat) or the **`/kb`** page
(collections management + full conversations).

See [`CLAUDE.md`](./CLAUDE.md) for the architecture diagram and the
Sandbox / FileStore / AgentRunner Protocol boundaries.

## Quick start

### 1. Backend

```bash
uv sync
uv run python -m workspace_app    # serves API + SPA on 127.0.0.1:8000
```

That entry point wires the production defaults:

- `LocalProcessSandbox` (subprocess-based; safe inside a VM/devcontainer)
- `SpecstarFileStore` (per-workspace blob via specstar)
- `LitellmAgentRunner` pointing at Ollama (see step 3)
- KB: a `LitellmEmbedder` for real semantic vectors + a retrieval LLM that
  enables multi-query / HyDE / rerank (env-configurable — see step 3)
- The React SPA from `web/dist` if it has been built

Swap any layer by importing `workspace_app.api.create_app` and passing
your own implementation of each Protocol.

### 2. Frontend

```bash
cd web
pnpm install
pnpm run dev                      # dev server on 5173, proxies API to 8000
# or for production:
pnpm run build                    # writes web/dist/, auto-mounted by the backend
```

### 3. Ollama (for the live LLM)

The default `LitellmAgentRunner` config targets
`ollama/qwen2.5-coder:7b-instruct`. Without Ollama the app still loads
and serves CRUD/UI, but the agent will fail when you actually send a
message.

The repo ships a `docker-compose.yml` that brings Ollama up as a sidecar
(easier than the systemd install — no `sudo`, isolates state in a named
volume, `docker compose down` cleans up):

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen2.5-coder:7b-instruct   # ≈ 4.7 GB
docker compose exec ollama ollama list                              # confirms
curl http://localhost:11434/api/tags                                # sanity check
```

For NVIDIA GPU acceleration, uncomment the `deploy:` block in
`docker-compose.yml` (requires `nvidia-container-toolkit` on the host).

If you prefer a host install instead, use the official one-liner:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5-coder:7b-instruct
```

#### KB models (embeddings + retrieval LLM)

The KB needs an **embedding** model (for vector search) and a **chat** model
(for the KB agent + query enhancements). Pull an embedder — `bge-m3` is a good
1024-dim default (matches `KB_EMBED_DIM`):

```bash
docker compose exec ollama ollama pull bge-m3        # 1024-dim, multilingual
docker compose exec ollama ollama pull qwen3:14b     # KB agent / retrieval LLM
```

Then point the app at the embedder you pulled (the default assumes a
`qwen3-embedding` tag, so set this explicitly for `bge-m3`):

```bash
KB_EMBED_MODEL=ollama/bge-m3 uv run python -m workspace_app
```

KB env vars (all optional except matching the embedder you pulled):

| var | default | notes |
| --- | --- | --- |
| `KB_EMBED_MODEL` | `ollama/qwen3-embedding` | LiteLLM model string for embeddings |
| `KB_EMBED_DIM` | `1024` | must equal the model's output width; re-index if changed |
| `KB_LLM_MODEL` | `ollama_chat/qwen3:14b` | KB agent + multi-query / HyDE / rerank |
| `KB_QUERY_PREFIX` / `KB_DOC_PREFIX` | `""` | asymmetric instruction prefixes (some embedders want them) |

If you swap to an embedder with a different dimension, set `KB_EMBED_DIM` to
match **and re-upload** documents — existing vectors are stored at the old
width. Without a real embedder the app falls back to a deterministic
non-semantic `HashEmbedder` (offline/tests only).

##### Turn off "thinking" on KB search (faster retrieval)

`kb_search`'s query enhancements — multi-query, HyDE, rerank — run on the
**retrieval LLM** (`kb.retrieval_llm`, default `qwen3:14b`). qwen3 emits a
`<think>` block by default, which burns tokens and latency on these cheap helper
calls. Turn it off with `reasoning_effort: none` on the `kb.retrieval_llm`
**usage entry** in `configs/config.yaml`:

```yaml
kb:
  retrieval_llm:
    preset: kb-retrieval
    reasoning_effort: none   # none → Ollama think=False; low|medium|high keep thinking
```

- Set it on the **`kb.retrieval_llm`** usage entry, *not* inside the
  `kb-retrieval` preset — the factory reads `reasoning_effort` straight off the
  usage entry, it isn't merged from the preset.
- Leaving it unset (`""`) omits the param, so the model keeps its default — and
  qwen3's default is to think. `none` maps to Ollama `think=False`.
- This only quiets the retrieval helpers; the KB chat agent's own thinking is a
  separate knob (its `kb_chat[]` preset).

Different models package `think=False` differently, so verify yours with the
live probe (needs a reachable Ollama — it drives the exact `LitellmLlm.stream`
path kb_search uses):

```bash
uv run python scripts/check_kb_reasoning.py ollama_chat/qwen3:14b --base-url http://localhost:11434
# verified: qwen3:14b & qwen3:8b → none = no-think, omit/low = THINKS.
# A "THINKS" on none means that model ignores think=False (needs a model-specific disable).
```

Once the model is pulled, the previously-skipped live smoke test should
pass:

```bash
uv run pytest tests/api/test_litellm_runner.py::test_live_run_against_ollama_emits_at_least_one_event -v
```

**Notes:**

- 7B class models on CPU only are slow — expect ≈ 30 s+ per turn. With
  an NVIDIA GPU, Ollama uses CUDA automatically.
- First inference after pulling has a load-into-RAM delay; subsequent
  calls are much faster.
- To swap the model, edit `AgentConfig.model` (defaults set in
  `src/workspace_app/resources/agent_config.py`). Anything LiteLLM
  understands works: `ollama/...`, `openai/gpt-4o-mini`,
  `anthropic/claude-sonnet-4-6`, etc.

## Tests

```bash
uv run coverage run -m pytest && uv run coverage report
uv run ruff check && uv run ruff format --check
uv run ty check
```

The Docker sandbox tests run against the local daemon when available and
auto-skip otherwise. The Ollama live test auto-skips when the daemon or
model isn't present.

## Project layout

```
src/workspace_app/
  sandbox/     Protocol + Mock / LocalProcess / Docker adapters
  filestore/   Protocol + SpecstarFileStore impl
  resources/   msgspec.Structs registered with specstar (incl. KB: Collection,
               SourceDoc, DocChunk, KbChat)
  agent/       Tool wrappers (exec, read/write/ls/exists/delete, kb_search,
               ask_knowledge_base)
  kb/          KB subsystem: chunker, embedder, ingest, retriever (hybrid),
               fusion/bm25/merge, query (multi-query/HyDE), rerank, citations,
               KB agent config + prompt
  api/         FastAPI app factory, SSE endpoint, AgentRunner Protocol,
               LitellmAgentRunner, kb_routes (collections/docs) + kb_chat_routes
               (threads + streaming chat)

web/
  src/         React SPA (Vite + TS): RCA chat + KB chat (shared agent-log
               rendering), collections management, document viewer
```

## Documentation

Full docs live in **[docs/](docs/README.md)** (繁體中文):

- **[architecture.md](docs/architecture.md)** — system design: layers/Protocols, agent-turn data flow, SSE events, sandbox/FileStore/sync lifecycle, user-ns isolation.
- **[development.md](docs/development.md)** — dev conventions, TDD workflow, how to add an SSE event / agent tool / file renderer.
- **[deployment.md](docs/deployment.md)** — deploy & swap in your own sandbox, agent runner, agent configs, and workspace templates via `create_app`.
- **[user-guide.md](docs/user-guide.md)** — RCA workflow + the VSCode-style UI, shortcuts.
- **[contract.md](docs/contract.md)** — authoritative HTTP routes + SSE event contract.
