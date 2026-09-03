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

The bundled model IS in litellm's registry at 40,960, which makes it a sharper
test than a made-up name: the proxy's answer has to BEAT a real catalog value,
so the assertion cannot pass by accident on a fallback.
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

#: What the bundled RCA preset names, and what the registry says about it.
BUNDLED_MODEL = "ollama_chat/qwen3:14b"
REGISTRY_SAYS = 40_960


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
        self._body = {"data": [{"model_name": BUNDLED_MODEL, "model_info": model_info}]}

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
