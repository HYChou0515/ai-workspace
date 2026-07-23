"""#624 P5: ask the endpoint, and survive every way it can decline to answer.

vLLM exposes `/tokenize`, which returns both an exact token count and
`max_model_len`. That is the only source that knows the truth for a self-hosted
model — no registry has its name, and both local estimators disagree with the
real tokenizer (measured on one Chinese string: ours 33, litellm's 58, real
~30-34).

But `/tokenize` is a vLLM extension, not part of the OpenAI-compatible spec —
proven by Ollama, whose `/v1/models` carries no length field at all. So the
probe's most-exercised path is the one where it gets nothing back, and that path
must be silent, cheap, and non-fatal: the whole P1-P4 design works without it.
"""

from __future__ import annotations

import json

import pytest

from workspace_app.context_probe import probe_context_limit


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body) if not isinstance(self._body, Exception) else ""


def _client(resp: object):
    """A minimal stand-in for the HTTP client: returns `resp`, or raises it."""

    def post(url: str, **kw: object) -> _Resp:
        if isinstance(resp, Exception):
            raise resp
        assert isinstance(resp, _Resp)
        return resp

    return type("C", (), {"post": staticmethod(post)})()


def test_a_vllm_answer_is_used():
    """The documented shape: `/tokenize` reports the model's own ceiling."""
    got = probe_context_limit(
        base_url="http://vllm",
        model="qwen3-14b",
        client=_client(_Resp(200, {"count": 6, "max_model_len": 32768})),
    )
    assert got == 32768


@pytest.mark.parametrize(
    "resp",
    [
        _Resp(404, {"detail": "Not Found"}),  # Ollama / any non-vLLM server
        _Resp(200, {"count": 6}),  # answered, but no ceiling in it
        _Resp(200, {"max_model_len": 0}),  # nonsense value
        _Resp(200, ["not", "a", "dict"]),  # unexpected shape
        _Resp(200, ValueError("not json")),  # unparseable body
        _Resp(500, {"detail": "boom"}),  # server error
        ConnectionError("refused"),  # nothing listening
        TimeoutError("slow"),  # hung endpoint
    ],
)
def test_every_way_of_getting_nothing_yields_none(resp):
    """This is the path #624's design is built around — `None` here is normal,
    not a failure, and must never raise: P1-P4 carry the whole feature without
    a working probe."""
    assert probe_context_limit(base_url="http://x", model="m", client=_client(resp)) is None


def test_no_base_url_is_not_probed():
    """Nothing to ask — don't invent a request."""
    assert probe_context_limit(base_url="", model="m", client=_client(_Resp(200, {}))) is None
    assert probe_context_limit(base_url=None, model="m", client=_client(_Resp(200, {}))) is None
