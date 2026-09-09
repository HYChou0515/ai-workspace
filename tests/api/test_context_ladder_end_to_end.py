"""The whole rung, from a running app to the number a chat reports.

Every other test on this feature holds one piece still and drives another. That
left the JOINS untested, and the joins are where this rung already failed once:
the first version resolved an unset per-preset endpoint to `None`, so it asked
nothing at all — on precisely the single-endpoint deployment it was written for
— while every unit test stayed green, because they all passed an explicit
base_url.

This holds nothing still except the network. It builds the app the way the
composition root does, with a real `LitellmAgentRunner` and the real probe, over
the bundled preset (`ollama_chat/qwen3:14b`, whose `llm_base_url` is `""` — the
exact shape that broke), and reads the answer off the same HTTP route an
operator would curl.

The catalog rung has to be a REAL value for these to mean anything — the
proxy's answer must BEAT one, not fill a hole — but it also has to be the same
value on every machine. The bundled preset names `ollama_chat/qwen3:14b`, and
litellm resolves an `ollama_chat/*` name by ASKING THE DAEMON: 40,960 on a
laptop with Ollama installed, nothing on CI. So the preset's model is overridden
to one litellm answers for out of its bundled map, with no network at all.
"""

from __future__ import annotations

import json

import pytest

from workspace_app.api import create_app
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item

#: A model litellm answers for from its BUNDLED map — no daemon, no network, the
#: same number everywhere. (`ollama_chat/*` names are resolved by asking the
#: Ollama daemon, which is why the app's own default cannot be used here.)
TEST_MODEL = "gpt-4o"
REGISTRY_SAYS = 128_000


class _Resp:
    def __init__(self, body: object) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> object:
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body)


class _Proxy:
    """A litellm proxy answering `/model/info` for one model."""

    def __init__(self, **model_info: object) -> None:
        self.asked: list[str] = []
        self.closed = False
        self._body = {"data": [{"model_name": TEST_MODEL, "model_info": model_info}]}

    def get(self, url: str, **kw: object) -> _Resp:
        self.asked.append(url)
        return _Resp(self._body)

    def close(self) -> None:
        self.closed = True


class _Nothing(_Proxy):
    """Not a litellm proxy — the ordinary case, and the one that must stay cheap
    and harmless."""

    def get(self, url: str, **kw: object) -> _Resp:
        self.asked.append(url)
        raise ConnectionError("no such route")


def _catalog_naming(model: str):
    """The bundled catalog with the RCA preset pointed at `model`.

    Everything else stays bundled — the preset's `llm.base_url` is still `""`,
    which is the shape this whole rung exists for and the one the first version
    got wrong."""
    import dataclasses

    from workspace_app.agent.config_catalog import AgentConfigCatalog
    from workspace_app.config.catalog_build import build_catalog
    from workspace_app.config.schema import Settings

    bundled = build_catalog(Settings(), config_dir=None)
    presets = dict(bundled.presets())
    presets["qwen3-local"] = dataclasses.replace(presets["qwen3-local"], model=model)
    return AgentConfigCatalog(presets=presets, kb_chats=bundled.kb_chats())


@pytest.fixture(autouse=True)
def _fresh_announcements():
    from workspace_app.api.turn_context import _CEILING_SAID

    _CEILING_SAID.clear()
    yield
    _CEILING_SAID.clear()


def _app_with(proxy, monkeypatch):
    from workspace_app import context_probe

    monkeypatch.setattr(context_probe, "_default_client", lambda timeout: proxy)
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=LitellmAgentRunner(base_url="http://the-deploys-proxy/v1"),
        get_user_id=lambda: "alice",
        agent_config_catalog=_catalog_naming(TEST_MODEL),
    )
    client = TestClient(app)
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()["chat_id"]
    return client, f"/a/rca/items/{iid}/chats/{chat}/context"


def test_what_the_proxy_declares_reaches_a_real_chat(monkeypatch):
    """The join, end to end. Every one of these makes it red:

    - `turn_ctx.endpoint_limits_fn = ...` missing from `create_app`
    - `base_url or self._base_url` missing from the runner (the F1 bug)
    - either `/model/info` spelling dropped
    - `declared` ranked below `catalog`
    """
    proxy = _Proxy(max_input_tokens=131_072, max_tokens=8_192)
    client, url = _app_with(proxy, monkeypatch)

    got = client.get(url).json()

    assert proxy.asked, "the proxy was never asked — the rung is silent again"
    assert proxy.asked[0].startswith("http://the-deploys-proxy"), proxy.asked
    assert got["limit"] == 131_072, got
    assert got["limit"] != REGISTRY_SAYS, "it has to BEAT the registry, not fall back to it"
    assert got["limit_source"] == "declared", got


def test_the_output_cap_is_never_mistaken_for_the_window(monkeypatch):
    """The proxy states BOTH. `max_tokens` there is 8,192 — an output cap — and
    it must not be read as, scaled into, or blended with the window."""
    proxy = _Proxy(max_input_tokens=131_072, max_tokens=8_192)
    client, url = _app_with(proxy, monkeypatch)

    assert client.get(url).json()["limit"] == 131_072


def test_a_silent_endpoint_leaves_the_ladder_where_it_was(monkeypatch):
    """Most endpoints are not a litellm proxy. That path must cost a moment and
    change nothing — here, the registry still answers."""
    proxy = _Nothing()
    client, url = _app_with(proxy, monkeypatch)

    got = client.get(url).json()

    assert proxy.asked, "it should still have tried"
    assert got["limit"] == REGISTRY_SAYS, got
    assert got["limit_source"] == "catalog", got


def test_the_proxy_is_asked_once_not_once_per_read(monkeypatch):
    """An HTTP round trip reached from a request path — every read of the usage
    gauge must not become one."""
    proxy = _Proxy(max_input_tokens=131_072)
    client, url = _app_with(proxy, monkeypatch)

    for _ in range(4):
        client.get(url)

    assert len(proxy.asked) == 1, proxy.asked
    assert proxy.closed, "and the client it opened is closed"
