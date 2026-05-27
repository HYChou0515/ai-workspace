"""Composition root — `Settings` + `get_*(settings) -> Protocol` factories.

The single place that decides *which* implementation backs each Protocol seam
(sandbox / filestore / runner / embedder / chunker / KB llm) + the specstar data
layer. Everything downstream (`create_app` and the app internals) depends only
on the Protocols, never on a concrete implementation or on `Settings`.

`__main__` reads `Settings.from_env()` and wires the factories into
`create_app`. Tests inject mocks/scripted impls directly and do NOT go through
these factories — the factories serve the production composition only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from specstar import SpecStar

from .api.litellm_runner import LitellmAgentRunner
from .api.runner import AgentRunner
from .filestore.memory import MemoryFileStore
from .filestore.protocol import FileStore
from .filestore.specstar_impl import SpecstarFileStore
from .kb.chunker import Chunker, FixedTokenChunker
from .kb.embedder import Embedder, HashEmbedder, LitellmEmbedder
from .kb.llm import ILlm, LitellmLlm
from .rca.agent import default_rca_agent_config
from .resources.kb import EMBED_DIM
from .sandbox.local_process import LocalProcessSandbox
from .sandbox.mock import MockSandbox
from .sandbox.protocol import Sandbox


def _flag(value: str | None) -> bool | None:
    """Tri-state: unset → None (auto-detect); set → truthy parse."""
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """All deployment knobs, read from the environment in one place."""

    default_user: str = "default-user"
    host: str = "127.0.0.1"
    port: int = 8000
    # External sub-path when behind a path-stripping proxy (e.g. "/my-svc/rca").
    # Only affects generated URLs (OpenAPI/docs); the SPA's own base path is a
    # build-time setting (VITE_BASE_PATH). Default "" = served at root.
    root_path: str = ""

    # sandbox (execution environment)
    sandbox_kind: str = "local"  # local | docker | mock
    sandbox_root: str | None = None
    exec_timeout: float = 60.0
    sandbox_isolate: bool | None = None  # None = auto-detect userns

    # file store
    filestore_kind: str = "memory"  # memory | specstar

    # agent runner (model + prompt come per-investigation from AgentConfig)
    runner_max_retries: int = 2
    runner_max_turns: int = 10

    # Chat LLM endpoint, shared by the RCA agent runner + the KB chat llm. Empty
    # → None → LiteLLM's own provider env / Ollama defaults (unchanged). The
    # model string's provider prefix still picks the provider; base_url just
    # overrides its host (e.g. `openai/<model>` + a hosted OpenAI-compatible
    # endpoint). The embedder has its OWN pair below — they don't share, so chat
    # can go hosted while embeddings stay on local Ollama.
    llm_base_url: str = ""
    llm_api_key: str = ""

    # read_file caps — a read past either is truncated with a notice (the agent
    # pages with offset/limit). Defaults sized for a large-context model; tighten
    # for a small local model. chars ≈ tokens × 4.
    read_file_max_lines: int = 2000
    read_file_max_chars: int = 200_000

    # Cross-turn memory: how many prior user/assistant messages to replay as the
    # agent's input each turn (windowed; generous for a large-context model).
    history_max_messages: int = 40

    # KB embedder ("" → offline HashEmbedder). dim is EMBED_DIM (import-time;
    # the DocChunk Vector width), never a separate knob — they must agree.
    # Default is bge-m3 (1024-dim, == EMBED_DIM): a strong multilingual embedder
    # that's commonly pulled. To use another model `ollama pull` it first and set
    # KB_EMBED_MODEL (+ KB_EMBED_DIM to its width) — a missing model 404s and the
    # doc lands in `error`.
    kb_embed_model: str = "ollama/bge-m3"
    kb_query_prefix: str = ""
    kb_doc_prefix: str = ""
    # Embedding HTTP resilience: a big doc's chunks are sent in batches, each
    # with a timeout + retries (a slow/loaded model can otherwise hang/time out).
    kb_embed_timeout: float = 60.0
    kb_embed_num_retries: int = 2
    kb_embed_batch_size: int = 64
    # Embedder endpoint (separate from the chat LLM above). Empty → None →
    # current Ollama/env behavior.
    kb_embed_base_url: str = ""
    kb_embed_api_key: str = ""

    # KB chunker
    kb_chunk_max_tokens: int = 256
    kb_chunk_overlap: int = 32

    # KB retrieval LLM ("" → None, disables multi-query / HyDE / rerank)
    kb_llm_model: str = "ollama_chat/qwen3:14b"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        import os

        e = os.environ if env is None else env
        d = cls()  # defaults
        return cls(
            default_user=e.get("DEFAULT_USER", d.default_user),
            host=e.get("APP_HOST", d.host),
            port=int(e.get("APP_PORT", str(d.port))),
            root_path=e.get("APP_ROOT_PATH", d.root_path),
            sandbox_kind=e.get("SANDBOX_KIND", d.sandbox_kind),
            sandbox_root=e.get("SANDBOX_ROOT", d.sandbox_root),
            exec_timeout=float(e.get("SANDBOX_EXEC_TIMEOUT", str(d.exec_timeout))),
            sandbox_isolate=_flag(e.get("SANDBOX_ISOLATE")),
            filestore_kind=e.get("FILESTORE_KIND", d.filestore_kind),
            runner_max_retries=int(e.get("RUNNER_MAX_RETRIES", str(d.runner_max_retries))),
            runner_max_turns=int(e.get("RUNNER_MAX_TURNS", str(d.runner_max_turns))),
            llm_base_url=e.get("LLM_BASE_URL", d.llm_base_url),
            llm_api_key=e.get("LLM_API_KEY", d.llm_api_key),
            read_file_max_lines=int(e.get("READ_FILE_MAX_LINES", str(d.read_file_max_lines))),
            read_file_max_chars=int(e.get("READ_FILE_MAX_CHARS", str(d.read_file_max_chars))),
            history_max_messages=int(e.get("HISTORY_MAX_MESSAGES", str(d.history_max_messages))),
            kb_embed_model=e.get("KB_EMBED_MODEL", d.kb_embed_model),
            kb_query_prefix=e.get("KB_QUERY_PREFIX", d.kb_query_prefix),
            kb_doc_prefix=e.get("KB_DOC_PREFIX", d.kb_doc_prefix),
            kb_embed_timeout=float(e.get("KB_EMBED_TIMEOUT", str(d.kb_embed_timeout))),
            kb_embed_num_retries=int(e.get("KB_EMBED_NUM_RETRIES", str(d.kb_embed_num_retries))),
            kb_embed_batch_size=int(e.get("KB_EMBED_BATCH_SIZE", str(d.kb_embed_batch_size))),
            kb_embed_base_url=e.get("KB_EMBED_BASE_URL", d.kb_embed_base_url),
            kb_embed_api_key=e.get("KB_EMBED_API_KEY", d.kb_embed_api_key),
            kb_chunk_max_tokens=int(e.get("KB_CHUNK_MAX_TOKENS", str(d.kb_chunk_max_tokens))),
            kb_chunk_overlap=int(e.get("KB_CHUNK_OVERLAP", str(d.kb_chunk_overlap))),
            kb_llm_model=e.get("KB_LLM_MODEL", d.kb_llm_model),
        )


def get_spec(settings: Settings) -> SpecStar:
    spec = SpecStar()
    spec.configure(default_user=settings.default_user, default_now=lambda: datetime.now(UTC))
    return spec


def get_sandbox(settings: Settings) -> Sandbox:
    match settings.sandbox_kind:
        case "mock":
            return MockSandbox()
        case "local":
            return LocalProcessSandbox(
                root_dir=Path(settings.sandbox_root) if settings.sandbox_root else None,
                exec_timeout=settings.exec_timeout,
                isolate=settings.sandbox_isolate,
            )
        case "docker":
            from .sandbox.docker import DockerSandbox

            return DockerSandbox()
        case other:
            raise ValueError(f"unknown SANDBOX_KIND: {other!r}")


def get_filestore(settings: Settings, spec: SpecStar) -> FileStore:
    match settings.filestore_kind:
        case "memory":
            return MemoryFileStore()
        case "specstar":
            return SpecstarFileStore(spec)
        case other:
            raise ValueError(f"unknown FILESTORE_KIND: {other!r}")


def get_runner(settings: Settings) -> AgentRunner:
    return LitellmAgentRunner(
        default_rca_agent_config(),
        max_retries=settings.runner_max_retries,
        max_turns=settings.runner_max_turns,
        base_url=settings.llm_base_url or None,
        api_key=settings.llm_api_key or None,
    )


def get_embedder(settings: Settings) -> Embedder:
    if settings.kb_embed_model:
        return LitellmEmbedder(
            settings.kb_embed_model,
            dim=EMBED_DIM,
            query_prefix=settings.kb_query_prefix,
            doc_prefix=settings.kb_doc_prefix,
            timeout=settings.kb_embed_timeout,
            num_retries=settings.kb_embed_num_retries,
            batch_size=settings.kb_embed_batch_size,
            base_url=settings.kb_embed_base_url or None,
            api_key=settings.kb_embed_api_key or None,
        )
    return HashEmbedder(dim=EMBED_DIM)


def get_chunker(settings: Settings) -> Chunker:
    return FixedTokenChunker(
        max_tokens=settings.kb_chunk_max_tokens, overlap_tokens=settings.kb_chunk_overlap
    )


def get_kb_llm(settings: Settings) -> ILlm | None:
    if not settings.kb_llm_model:
        return None
    return LitellmLlm(
        settings.kb_llm_model,
        base_url=settings.llm_base_url or None,
        api_key=settings.llm_api_key or None,
    )
