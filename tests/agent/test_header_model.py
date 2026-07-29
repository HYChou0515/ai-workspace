"""HeaderModel — puts one endpoint's credential headers (a session cookie, a lane
tag) on that endpoint's requests, without touching the turn's shared ModelSettings.

The SDK reaches a model two ways and does NOT agree with itself about calling
convention: `stream_response` gets `model_settings` POSITIONALLY (3rd arg),
`get_response` by KEYWORD. Both have to arrive with the headers attached.
"""

from typing import Any

from agents.model_settings import ModelSettings
from agents.models.interface import Model

from workspace_app.agent.header_model import HeaderModel


class _RecordingModel(Model):
    """Records the ModelSettings it was handed, however it was called."""

    def __init__(self) -> None:
        self.settings: list[ModelSettings] = []

    def _record(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        ms = kwargs.get("model_settings", args[2] if len(args) > 2 else None)
        assert isinstance(ms, ModelSettings)
        self.settings.append(ms)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        self._record(args, kwargs)
        return "response"

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        self._record(args, kwargs)
        for chunk in ("a", "b"):
            yield chunk


async def test_headers_reach_the_inner_model_on_the_streaming_path():
    inner = _RecordingModel()
    model = HeaderModel(inner, {"Cookie": "session=abc"})
    # positional, exactly as the SDK's run loop calls it
    chunks = [c async for c in model.stream_response("sys", [], ModelSettings(), [])]
    assert chunks == ["a", "b"]
    assert inner.settings[0].extra_headers == {"Cookie": "session=abc"}


async def test_headers_reach_the_inner_model_on_the_non_streaming_path():
    inner = _RecordingModel()
    model = HeaderModel(inner, {"Cookie": "session=abc"})
    # by keyword, exactly as the SDK's run loop calls it
    assert await model.get_response(model_settings=ModelSettings(), input=[]) == "response"
    assert inner.settings[0].extra_headers == {"Cookie": "session=abc"}


async def test_the_turns_own_headers_survive_and_the_credential_wins_a_clash():
    inner = _RecordingModel()
    model = HeaderModel(inner, {"Cookie": "fresh", "X-Lane": "background"})
    settings = ModelSettings(extra_headers={"Cookie": "stale", "X-Trace": "t1"})
    [c async for c in model.stream_response("sys", [], settings, [])]
    assert inner.settings[0].extra_headers == {
        "Cookie": "fresh",  # the credential is the authority on auth
        "X-Lane": "background",
        "X-Trace": "t1",  # unrelated header untouched
    }


async def test_the_shared_settings_object_is_not_mutated():
    # A FallbackModel hands the SAME ModelSettings to every endpoint it may switch
    # to. Mutating it in place would leak one gateway's session cookie to the next
    # host in the chain — so each endpoint gets its own copy.
    inner = _RecordingModel()
    settings = ModelSettings()
    [c async for c in HeaderModel(inner, {"Cookie": "a"}).stream_response("s", [], settings, [])]
    assert settings.extra_headers is None
    [c async for c in HeaderModel(inner, {"Cookie": "b"}).stream_response("s", [], settings, [])]
    assert inner.settings[0].extra_headers == {"Cookie": "a"}
    assert inner.settings[1].extra_headers == {"Cookie": "b"}


async def test_unknown_attributes_pass_through_to_the_inner_model():
    # the #69 trace reads `.model` off whatever model object it is handed
    inner = _RecordingModel()
    inner.model = "qwen3-14b"  # ty: ignore[unresolved-attribute]
    assert HeaderModel(inner, {"Cookie": "a"}).model == "qwen3-14b"
