"""Whose work this is (docs/plan-capacity-usage.md §6.2–§6.4, §7).

Each unit of work sets a scope when it starts — a turn, a job attempt, a
request — and everything it calls reads it: the LLM adapters put it in
`x-litellm-tags`. ContextVars carry it across awaits and into
`asyncio.to_thread`; a bare `run_in_executor` does not copy the context, and
work started there has no scope.

A request is different: who made it is only known once a route asks
`get_user_id`. So the request carries a `RequestIdentity` holder, and
`recording_reader` (the `get_user_id` that `create_app` hands its routes)
writes the first answer into it. The holder is closed when the request ends:
tasks the request started copied the context, holder included, and a closed
holder neither attributes their later work nor takes a later answer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

#: A request whose person could not be resolved (E4).
UNKNOWN = "unknown"
#: Work no person asked for: a sweeper, a health check (E4).
SYSTEM = "system"


@dataclass(frozen=True)
class UsageScope:
    user: str
    app: str = ""
    item: str = ""
    purpose: str = ""
    #: Whether LLM calls in this scope carry `x-litellm-tags` (`capacity.llm_tags`).
    tags: bool = True


class RequestIdentity:
    """Who one request is for — filled in by the first `get_user_id` answer
    given while the request runs, and closed when it ends."""

    def __init__(self, app: str = "", *, tags: bool = True) -> None:
        self.app = app
        self.tags = tags
        self.user: str | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


_SCOPE: ContextVar[UsageScope | None] = ContextVar("usage_scope", default=None)
_REQUEST: ContextVar[RequestIdentity | None] = ContextVar("usage_request", default=None)


def current() -> UsageScope | None:
    """The scope of the work running here: its own, else its request's, else none."""
    scope = _SCOPE.get()
    if scope is not None:
        return scope
    holder = _REQUEST.get()
    if holder is None or holder.closed:
        return None
    return UsageScope(
        user=holder.user or UNKNOWN, app=holder.app, purpose="request", tags=holder.tags
    )


@contextmanager
def scoped(scope: UsageScope) -> Iterator[None]:
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


@contextmanager
def request_identity(holder: RequestIdentity) -> Iterator[RequestIdentity]:
    token = _REQUEST.set(holder)
    try:
        yield holder
    finally:
        _REQUEST.reset(token)


def recording_reader(get_user_id: Callable[[], str]) -> Callable[[], str]:
    """`get_user_id`, telling the running request (if any) who it is."""

    def reader() -> str:
        user = get_user_id()
        holder = _REQUEST.get()
        if holder is not None and not holder.closed and holder.user is None:
            holder.user = user
        return user

    return reader
