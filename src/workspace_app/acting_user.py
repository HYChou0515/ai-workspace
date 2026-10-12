"""Who a request acts as, when it is not who its cookie says.

A deploy reads the current user from its own credential — `get_spec(settings,
get_user_id=<cookie reader>)` — and every specstar call not wrapped in
`rm.using(user=…)` asks that reader. One route has a credential of its own
instead: the WUI content route (`api/wui_content.py`), whose opaque-origin
frame sends no cookie at all and authorises by a pass. Having checked the pass,
it declares the pass's holder here, and every reader the app was given answers
with that holder for the rest of that request (a `ContextVar`: per request,
carried into `asyncio.to_thread`, gone when the request ends).

The override is applied where the reader is handed in — `make_spec` wraps it
(`honouring`) — so a deploy's reader needs no change. Only specstar's reads ask
it inside that route; the app-side `get_user_id` is not consulted there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_ACTING: ContextVar[str | None] = ContextVar("acting_user", default=None)


@contextmanager
def acting_as(user: str) -> Iterator[None]:
    """For the duration of the block, the current user is `user`."""
    token = _ACTING.set(user)
    try:
        yield
    finally:
        _ACTING.reset(token)


def honouring(reader: Callable[[], str]) -> Callable[[], str]:
    """`reader`, unless the request declared whom it acts as."""
    if getattr(reader, "_honours_acting", False):
        return reader

    def current_user() -> str:
        acting = _ACTING.get()
        return acting if acting is not None else reader()

    current_user._honours_acting = True  # ty: ignore[unresolved-attribute]
    return current_user
