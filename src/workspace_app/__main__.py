"""Default entry point: `uv run python -m workspace_app`.

Thin composition root: read `Settings` from the environment, build each
Protocol implementation via the `factories.get_*` functions, and wire them into
`create_app`. To change which implementation backs a seam, set the matching
env var (see `factories.Settings`) — no code change. To compose differently in
code, import `workspace_app.factories` (or `create_app`) directly.

Common env vars (all optional; see `factories.Settings` for the full list):
  SANDBOX_KIND=local|docker|mock      FILESTORE_KIND=memory|specstar
  KB_EMBED_MODEL=ollama/bge-m3        KB_EMBED_DIM=1024
  KB_LLM_MODEL=ollama_chat/qwen3:14b  ("" disables multi-query/HyDE/rerank)
  APP_HOST / APP_PORT
"""

from __future__ import annotations

import uvicorn

from workspace_app.api import create_app
from workspace_app.factories import (
    Settings,
    get_chunker,
    get_embedder,
    get_filestore,
    get_kb_llm,
    get_runner,
    get_sandbox,
    get_spec,
)
from workspace_app.monitor import SpecstarMonitor


def main() -> None:
    settings = Settings.from_env()
    spec = get_spec(settings)
    app = create_app(
        spec=spec,
        sandbox=get_sandbox(settings),
        filestore=get_filestore(settings, spec),
        runner=get_runner(settings),
        kb_embedder=get_embedder(settings),
        kb_chunker=get_chunker(settings),
        kb_llm=get_kb_llm(settings),
        monitor=SpecstarMonitor(spec),  # persist LLM/agent telemetry (issue #11)
        root_path=settings.root_path,
        read_file_max_lines=settings.read_file_max_lines,
        read_file_max_chars=settings.read_file_max_chars,
        history_max_messages=settings.history_max_messages,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
