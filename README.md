# workspace-app

A team-internal web app for running OpenAI Agents inside per-workspace
sandboxes. Each workspace owns a persistent file store; a sandbox is
created on demand when the agent needs to run a shell command, and is
killed when idle.

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
  resources/   msgspec.Structs registered with specstar
  agent/       Tool wrappers (exec, read/write/ls/exists/delete)
  api/         FastAPI app factory, SSE endpoint, AgentRunner Protocol,
               LitellmAgentRunner

web/
  src/         React SPA (Vite + TS): chat page that streams SSE
```

## Deployment & customization

See **[docs/deployment.md](docs/deployment.md)** (繁體中文) for how to deploy and
swap in your own sandbox, agent runner, agent configs, and workspace templates
via `create_app`.
