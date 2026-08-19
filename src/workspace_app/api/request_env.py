"""IRequestEnv — the seam between the HTTP request behind a chat send and the
environment variables that request's turn hands to its tools (#714).

The item's ``env_vars`` (#673) already reach the tools, but they are ONE SHARED
COPY: stored on the item, plainly readable by every participant, and identical
no matter who pressed send. What they cannot carry is the thing that differs per
person — the caller's own SSO session cookie, a header their gateway stamped on
— because putting that on the item would hand one person's credential to
everyone the item is shared with.

So this seam exists to compose those per-request values, and the values it
returns are NEVER written back anywhere. They live for exactly one turn: the one
started by the request they were read from.

The platform ships no implementation and knows no cookie name. Which cookie,
which header, and what the values mean belong to the deploy's gateway, so the
entire decision — including the whitelist — lives inside the impl a deploy names
in ``server.request_env``. Nothing here inspects the request.
"""

from __future__ import annotations

import abc

from fastapi import Request


class IRequestEnv(abc.ABC):
    """Compose one turn's request-derived environment variables.

    Resolved once at startup from the ``server.request_env`` dotted path, so the
    impl must be constructible with no arguments.
    """

    @abc.abstractmethod
    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        """The variables this request's tools should be given.

        Called on the send path, while the request is still open — a turn runs
        as a background task, so by the time a tool is dispatched there is no
        request left to read. Returning ``{}`` is the way to say "nothing for
        this caller".

        ``async`` even though a cookie-parsing impl needs no await: exchanging a
        cookie for a token against an external system is the case this seam was
        opened for, and it must not require changing the interface later. That
        exchange sits between the user pressing send and the turn starting, so
        its latency is the send's latency.

        RAISING FAILS THE WHOLE SEND — the turn does not run and the caller is
        told. That is deliberate: what travels here is identity, and quietly
        substituting "no credential" would let the turn proceed as somebody else
        and return an answer that looks right. An impl that prefers to degrade
        must catch its own errors and return ``{}``, because only the impl knows
        whether running without the value is meaningful.

        The values are NOT the whole story of what a tool sees: the item's own
        ``env_vars`` are merged on top, so a name set in both places resolves to
        the item's value.
        """
        ...
