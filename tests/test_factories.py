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


def test_get_chunker_and_kb_llm():
    assert isinstance(get_chunker(Settings(kb_chunk_max_tokens=99)), FixedTokenChunker)
    assert isinstance(get_kb_llm(Settings(kb_llm_model="ollama_chat/qwen3:14b")), LitellmLlm)
    assert get_kb_llm(Settings(kb_llm_model="")) is None  # disabled


def test_get_runner_is_litellm():
    assert isinstance(get_runner(Settings()), LitellmAgentRunner)
