"""LLM calls carry their person's tags to LiteLLM (docs/plan-capacity-usage.md §7).

`x-litellm-tags: ws,ws.user:<id>,ws.app:<slug>,ws.purpose:<p>,ws.call:<kind>`,
read from the usage scope at the moment of the call. LiteLLM stores the list in
its spend log's `request_tags`, which is how ④ splits GPU use per person: the
non-agent calls share one key, so LiteLLM cannot tell whose they are otherwise.

A call outside any scope (a sweeper, a health check) carries no header and
shows as untagged traffic; so does code inside a sandbox calling LiteLLM
itself. A scope opts out with `tags=False` (`capacity.llm_tags: false`) — for
a LiteLLM that routes on tags with no `default` pool, which would refuse
requests whose tags match no deployment. No item tag: thousands of distinct
item ids would become thousands of rows in LiteLLM's per-tag tables.

Values are percent-encoded so a comma in a name cannot split the list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from agents.models.interface import Model

from ..agent.header_model import merge_extra_headers
from .scope import current

TAG_HEADER = "x-litellm-tags"
#: The one tag every call of this platform carries, so ④ can tell its traffic
#: from other systems' on the same LiteLLM.
MARKER = "ws"


def tag_headers(call: str) -> dict[str, str]:
    """The header for an LLM call made now (`call`: agent | llm | vlm | embed),
    or nothing outside a scope that tags."""
    scope = current()
    if scope is None or not scope.tags:
        return {}
    tags = [MARKER, f"ws.user:{quote(scope.user, safe='')}"]
    if scope.app:
        tags.append(f"ws.app:{quote(scope.app, safe='')}")
    tags.append(f"ws.purpose:{quote(scope.purpose or 'request', safe='')}")
    tags.append(f"ws.call:{call}")
    return {TAG_HEADER: ",".join(tags)}


def tagged(call: str) -> dict[str, Any]:
    """`litellm.completion(..., **tagged("llm"))`: `extra_headers` when there are tags."""
    headers = tag_headers(call)
    return {"extra_headers": headers} if headers else {}


class TaggedModel(Model):
    """The agent's model, every request it makes tagged from the scope of the
    turn making it. Per call, not per construction: one model may serve turns
    of different people."""

    def __init__(self, inner: Model) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _tag(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        headers = tag_headers("agent")
        return merge_extra_headers(args, kwargs, headers) if headers else (args, kwargs)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._tag(args, kwargs)
        return await self._inner.get_response(*args, **kwargs)

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        args, kwargs = self._tag(args, kwargs)
        async for chunk in self._inner.stream_response(*args, **kwargs):
            yield chunk
