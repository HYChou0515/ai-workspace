"""Put one endpoint's credential headers on that endpoint's requests.

A user's LLM credential is not always a bearer token: a deploy may authenticate to
its gateway with a session cookie, and may want the call's lane (is a person waiting
on this?) tagged so the gateway can rate-limit background work more tightly than
interactive work. Both are HTTP headers, and litellm forwards ``extra_headers``
verbatim on every provider path we use.

Why a model wrapper rather than the turn's ``ModelSettings``: headers belong to ONE
endpoint's credential, while a turn has ONE ``ModelSettings`` that a
:class:`~workspace_app.failover.model.FallbackModel` hands to whichever endpoint it
switches to. Setting them there would send the first gateway's session cookie to the
next host in the chain. So each endpoint's model carries its own headers, and the
shared settings object is copied — never mutated — on the way through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from typing import Any

from agents.model_settings import ModelSettings
from agents.models.interface import Model


class HeaderModel(Model):
    """Wrap ``inner`` so every request it makes carries ``headers``.

    The turn's own ``extra_headers`` are kept; the credential's win a clash, since
    they are what authenticates the call. Everything else delegates untouched."""

    def __init__(self, inner: Model, headers: Mapping[str, str]) -> None:
        self._inner = inner
        self._headers = dict(headers)

    def __getattr__(self, name: str) -> Any:
        # Transparent passthrough for everything we don't override (e.g. the
        # `.model` id the #69 trace reads). Only fires for missing attributes.
        return getattr(self._inner, name)

    def _with_headers(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """Return ``(args, kwargs)`` with the call's ModelSettings replaced by a COPY
        carrying our headers.

        The SDK's run loop does not agree with itself about how it passes them:
        ``stream_response`` sends ``model_settings`` as the 3rd POSITIONAL argument,
        ``get_response`` sends it by KEYWORD. Miss either and the headers silently
        never reach the wire, which looks exactly like a gateway rejecting the
        user's credential."""
        if "model_settings" in kwargs:
            settings = kwargs["model_settings"]
            merged = replace(
                settings, extra_headers={**(settings.extra_headers or {}), **self._headers}
            )
            return args, {**kwargs, "model_settings": merged}
        if len(args) > 2 and isinstance(args[2], ModelSettings):
            settings = args[2]
            merged = replace(
                settings, extra_headers={**(settings.extra_headers or {}), **self._headers}
            )
            return (*args[:2], merged, *args[3:]), kwargs
        return args, kwargs  # pragma: no cover - the SDK always passes settings

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._with_headers(args, kwargs)
        return await self._inner.get_response(*args, **kwargs)

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        args, kwargs = self._with_headers(args, kwargs)
        async for chunk in self._inner.stream_response(*args, **kwargs):
            yield chunk
