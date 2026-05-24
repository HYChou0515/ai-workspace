"""Default entry point: `uv run python -m workspace_app`.

Wires the RCA defaults:
  - LocalProcessSandbox (works in any VM/devcontainer without docker)
  - SpecstarFileStore (in-process)
  - LitellmAgentRunner pre-loaded with the RCA system prompt
  - KB: a LiteLLM embedder (real semantic vectors) + a retrieval LLM that turns
    on multi-query / HyDE / rerank inside kb_search
  - SPA at web/dist if built

Override any piece by importing `workspace_app.api.create_app` directly.

KB models are env-configurable (defaults assume local Ollama). The embedder's
output width MUST equal KB_EMBED_DIM (default 1024) — change both together and
re-index if you swap to a model with a different dimension:
  - KB_EMBED_MODEL  (default "ollama/qwen3-embedding")
  - KB_EMBED_DIM    (default 1024, read by resources.kb.EMBED_DIM)
  - KB_LLM_MODEL    (default "ollama_chat/qwen3:14b") — retrieval enhancements
  - KB_QUERY_PREFIX / KB_DOC_PREFIX (default "") — asymmetric instruction prefixes
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import uvicorn
from specstar import SpecStar

from workspace_app.api import create_app
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import LitellmEmbedder
from workspace_app.kb.llm import LitellmLlm
from workspace_app.rca.agent import default_rca_agent_config
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.local_process import LocalProcessSandbox


def main() -> None:
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    kb_embedder = LitellmEmbedder(
        os.getenv("KB_EMBED_MODEL", "ollama/qwen3-embedding"),
        dim=EMBED_DIM,
        query_prefix=os.getenv("KB_QUERY_PREFIX", ""),
        doc_prefix=os.getenv("KB_DOC_PREFIX", ""),
    )
    kb_llm = LitellmLlm(os.getenv("KB_LLM_MODEL", "ollama_chat/qwen3:14b"))
    app = create_app(
        spec=spec,
        sandbox=LocalProcessSandbox(),
        # MemoryFileStore: in-process, no persistence on restart.
        # Swap to SpecstarFileStore(spec) for spec-backed persistence
        # at the cost of ~19 internal /-workspacefiles CRUD routes
        # appearing in /openapi.json.
        filestore=MemoryFileStore(),
        runner=LitellmAgentRunner(default_rca_agent_config()),
        kb_embedder=kb_embedder,
        kb_llm=kb_llm,
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
