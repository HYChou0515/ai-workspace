"""Composition-root factories: Settings.from_env + get_* return the Protocol
implementation chosen by settings, so downstream depends only on the Protocol."""

import pytest

from workspace_app.api.litellm_runner import LitellmAgentRunner
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
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder, LitellmEmbedder
from workspace_app.kb.llm import LitellmLlm
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.mock import MockSandbox


def test_settings_defaults_and_env_parsing():
    assert Settings.from_env({}) == Settings()  # empty env → all defaults
    s = Settings.from_env(
        {
            "DEFAULT_USER": "alice",
            "SANDBOX_KIND": "docker",
            "SANDBOX_EXEC_TIMEOUT": "12.5",
            "SANDBOX_ISOLATE": "false",
            "FILESTORE_KIND": "specstar",
            "RUNNER_MAX_TURNS": "20",
            "KB_EMBED_MODEL": "ollama/bge-m3",
            "KB_LLM_MODEL": "",
            "KB_CHUNK_MAX_TOKENS": "128",
        }
    )
    assert s.default_user == "alice"
    assert s.sandbox_kind == "docker"
    assert s.exec_timeout == 12.5
    assert s.sandbox_isolate is False
    assert s.filestore_kind == "specstar"
    assert s.runner_max_turns == 20
    assert s.kb_embed_model == "ollama/bge-m3"
    assert s.kb_llm_model == ""
    assert s.kb_chunk_max_tokens == 128


def test_llm_endpoint_settings_from_env():
    assert Settings.from_env({}).llm_base_url == ""  # default: unset
    assert Settings.from_env({}).kb_embed_api_key == ""
    s = Settings.from_env(
        {
            "LLM_BASE_URL": "https://hosted/v1",
            "LLM_API_KEY": "sk-chat",
            "KB_EMBED_BASE_URL": "http://localhost:11434",
            "KB_EMBED_API_KEY": "ek-embed",
        }
    )
    assert s.llm_base_url == "https://hosted/v1"
    assert s.llm_api_key == "sk-chat"
    assert s.kb_embed_base_url == "http://localhost:11434"
    assert s.kb_embed_api_key == "ek-embed"


def test_sandbox_isolate_tristate():
    assert Settings.from_env({}).sandbox_isolate is None  # unset → auto-detect
    assert Settings.from_env({"SANDBOX_ISOLATE": "1"}).sandbox_isolate is True


def test_get_spec_configures_default_user():
    spec = get_spec(Settings(default_user="bob"))
    assert spec.default_user == "bob"


def test_get_sandbox_dispatch(monkeypatch):
    assert isinstance(get_sandbox(Settings(sandbox_kind="mock")), MockSandbox)
    assert isinstance(get_sandbox(Settings(sandbox_kind="local")), LocalProcessSandbox)

    import docker

    monkeypatch.setattr(docker, "from_env", lambda: object())  # no daemon needed
    from workspace_app.sandbox.docker import DockerSandbox

    assert isinstance(get_sandbox(Settings(sandbox_kind="docker")), DockerSandbox)

    with pytest.raises(ValueError):
        get_sandbox(Settings(sandbox_kind="bogus"))


def test_get_filestore_dispatch():
    spec = get_spec(Settings())
    assert isinstance(get_filestore(Settings(filestore_kind="memory"), spec), MemoryFileStore)
    assert isinstance(get_filestore(Settings(filestore_kind="specstar"), spec), SpecstarFileStore)
    with pytest.raises(ValueError):
        get_filestore(Settings(filestore_kind="bogus"), spec)


def test_get_embedder_dispatch_uses_embed_dim():
    real = get_embedder(Settings(kb_embed_model="ollama/bge-m3"))
    assert isinstance(real, LitellmEmbedder) and real.dim == EMBED_DIM
    offline = get_embedder(Settings(kb_embed_model=""))  # empty → offline hash
    assert isinstance(offline, HashEmbedder) and offline.dim == EMBED_DIM


def test_get_embedder_threads_its_own_endpoint():
    # Separate from the chat pair so embeddings can stay on local Ollama.
    s = Settings(
        kb_embed_model="ollama/bge-m3", kb_embed_base_url="http://o/v1", kb_embed_api_key="ek"
    )
    e = get_embedder(s)
    assert isinstance(e, LitellmEmbedder) and e._base_url == "http://o/v1" and e._api_key == "ek"
    bare = get_embedder(Settings(kb_embed_model="ollama/bge-m3"))
    assert isinstance(bare, LitellmEmbedder) and bare._base_url is None and bare._api_key is None


def test_get_chunker_and_kb_llm():
    assert isinstance(get_chunker(Settings(kb_chunk_max_tokens=99)), FixedTokenChunker)
    assert isinstance(get_kb_llm(Settings(kb_llm_model="ollama_chat/qwen3:14b")), LitellmLlm)
    assert get_kb_llm(Settings(kb_llm_model="")) is None  # disabled


def test_get_kb_llm_threads_chat_endpoint():
    # KB chat llm shares the chat pair (llm_*), empty → None.
    s = Settings(kb_llm_model="ollama_chat/q", llm_base_url="http://x/v1", llm_api_key="k")
    llm = get_kb_llm(s)
    assert llm is not None and llm._base_url == "http://x/v1" and llm._api_key == "k"
    bare = get_kb_llm(Settings(kb_llm_model="ollama_chat/q"))
    assert bare is not None and bare._base_url is None and bare._api_key is None


def test_get_runner_is_litellm():
    assert isinstance(get_runner(Settings()), LitellmAgentRunner)


def test_get_runner_threads_chat_endpoint_empty_is_none():
    r = get_runner(Settings(llm_base_url="https://hosted/v1", llm_api_key="sk-1"))
    assert r._base_url == "https://hosted/v1" and r._api_key == "sk-1"
    bare = get_runner(Settings())  # unset → None, not "" (LiteLLM defaults apply)
    assert bare._base_url is None and bare._api_key is None
