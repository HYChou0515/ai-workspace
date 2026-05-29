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
  LLM_BASE_URL / LLM_API_KEY          (chat: RCA agent + KB chat; "" → LiteLLM defaults)
  KB_EMBED_BASE_URL / KB_EMBED_API_KEY  (embedder endpoint; separate from chat)
  APP_HOST / APP_PORT
"""

from __future__ import annotations

import uvicorn

from workspace_app.api import create_app
from workspace_app.factories import (
    Settings,
    get_chat_pipeline,
    get_code_embedder,
    get_doc_pipeline,
    get_embedder,
    get_filestore,
    get_kb_llm,
    get_runner,
    get_sandbox,
    get_spec,
)
from workspace_app.monitor import SpecstarMonitor
from workspace_app.rca.sample_tools import PREBUILT_DIR, available_sample_tools


def main() -> None:
    settings = Settings.from_env()
    spec = get_spec(settings)
    # Deploy-level provisioned tools. Only those whose prebuilt package exists
    # are advertised (run `scripts/prebuild_tools.py`); a real deployment swaps
    # this for its own ToolDefs. They're gated per-investigation by the agent
    # config's allowed_tools, so the tool-demo template is what turns them on.
    tool_defs = available_sample_tools()
    # The sandbox mounts the prebuilt dir read-only at /.tools (outside the
    # workspace) — no per-sandbox copy. Only point at it once it's built.
    tools_dir = PREBUILT_DIR if tool_defs else None
    embedder = get_embedder(settings)
    kb_llm = get_kb_llm(settings)
    app = create_app(
        spec=spec,
        sandbox=get_sandbox(settings, tools_dir=tools_dir),
        filestore=get_filestore(settings, spec),
        runner=get_runner(settings),
        kb_embedder=embedder,
        # P3.0: code-specialised embedder (`KB_CODE_EMBED_MODEL`); None ⇒ code
        # collections fall back to the default embedder.
        kb_code_embedder=get_code_embedder(settings),
        # P1: LlamaIndex IngestionPipeline replaces the hand-rolled chunker.
        # Tests/offline runs still pass `kb_chunker=` directly to create_app.
        kb_pipeline=get_doc_pipeline(settings, embedder),
        # P2: chat → knowledge insight extraction (None when no KB llm wired).
        kb_chat_pipeline=get_chat_pipeline(settings, embedder, kb_llm),
        kb_llm=kb_llm,
        monitor=SpecstarMonitor(spec),  # persist LLM/agent telemetry (issue #11)
        root_path=settings.root_path,
        read_file_max_lines=settings.read_file_max_lines,
        read_file_max_chars=settings.read_file_max_chars,
        history_max_messages=settings.history_max_messages,
        tool_defs=tool_defs,
    )
    if tool_defs:
        names = ", ".join(t.name for t in tool_defs)
        print(f"  provisioned tools available (tool-demo template): {names}")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
