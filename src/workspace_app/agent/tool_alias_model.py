"""ToolAliasModel — a call to a third-party command by an old name, renamed at
the model-output boundary (docs/plan-third-party-tool-names.md N2).

The model is given `a__list-files`, but a skill or a page written before the
prefix tells it `list-files` — and the SDK ends the whole turn on a call to a
name it does not hold (`ModelBehaviorError: Tool … not found`). So, like
`RepairingModel`, this rewrites the call before the SDK records it:

- a name exactly one granted command answers to → that command's name;
- a name two answer to → the first of them, with arguments that make the tool
  wrap answer with the collision (`args_recovery.ambiguous_call_reply`)
  instead of running anything. That call fails; the turn goes on.

The alias table comes from the turn's own grants (`registry.third_party_aliases`)
minus every name the model was actually given — a built-in or a first-party
command of that name is what the name means (D6).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

from agents import Model

from .arg_repair import AMBIGUOUS_CALL_KEY

_LOGGER = logging.getLogger(__name__)

#: Old name → the `(local name, model name)` of every command answering to it.
Aliases = Mapping[str, tuple[tuple[str, str], ...]]


def _rename(item: Any, aliases: Aliases) -> None:
    if getattr(item, "type", None) != "function_call":
        return
    called = getattr(item, "name", None)
    targets = aliases.get(called) if isinstance(called, str) else None
    if not targets:
        return
    if len(targets) == 1:
        item.name = targets[0][1]
        _LOGGER.info("tool_alias: %r called by its old name; dispatching %s", called, item.name)
        return
    item.name = targets[0][1]
    item.arguments = json.dumps(
        {AMBIGUOUS_CALL_KEY: {"called": called, "candidates": [list(t) for t in targets]}}
    )
    _LOGGER.info("tool_alias: %r names %d commands; that call fails", called, len(targets))


def _rename_event(event: Any, aliases: Aliases) -> Any:
    kind = getattr(event, "type", None)
    if kind == "response.output_item.done":
        _rename(getattr(event, "item", None), aliases)
    elif kind == "response.completed":
        for item in getattr(getattr(event, "response", None), "output", None) or []:
            _rename(item, aliases)
    return event


class ToolAliasModel(Model):
    """Wrap a Model so a call by an old third-party name reaches its command."""

    def __init__(self, inner: Model, aliases: Aliases) -> None:
        self._inner = inner
        self._aliases = aliases

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        response = await self._inner.get_response(*args, **kwargs)
        for item in getattr(response, "output", None) or []:
            _rename(item, self._aliases)
        return response

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for chunk in self._inner.stream_response(*args, **kwargs):
            yield _rename_event(chunk, self._aliases)
