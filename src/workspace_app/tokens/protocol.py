"""ITokenService — the seam between a user id and the credential that authenticates
one LLM endpoint call made on that user's behalf."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Literal

CallLane = Literal["interactive", "background"]
"""Which kind of call this is, from the user's point of view.

- ``interactive`` — a person is waiting on this answer (they just hit send).
- ``background`` — work the system does on their behalf with nobody watching (a
  workflow step, card generation, a wiki pass).

The distinction exists because the two deserve different rate limits: a batch job
must not spend the quota a person is waiting on. We do not enforce that here — the
external gateway does — so the lane's only job is to reach the wire, via whatever
header the credential source decides to put it in.

``background`` is the DEFAULT everywhere (``AgentToolContext.call_lane``), because
an unmarked call getting the tighter quota is merely a slower answer, while the
reverse is a batch job quietly eating the interactive allowance — the exact thing
the lane was introduced to stop.
"""


@dataclass(frozen=True)
class LlmCredential:
    """What it takes to authenticate one LLM call: an ``api_key``, extra ``headers``,
    or both.

    ``api_key`` keeps the meaning it has everywhere else (``str`` = use this key,
    ``None`` = no explicit key, e.g. local Ollama). ``headers`` are merged into the
    request's HTTP headers as-is — litellm forwards them verbatim on every provider
    path we use (``openai/*`` → ``/chat/completions``, ``ollama*`` → ``/api/chat``).
    That is how a deploy authenticates with something that is not a bearer token (a
    session cookie) and how it tags the call's :data:`CallLane` for the gateway's
    rate limiter.

    We deliberately do NOT name any header here. Which cookie, which lane header and
    what the values look like all belong to the credential source; this app is only
    the courier."""

    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class ITokenService(abc.ABC):
    """Resolve the credential to use for one LLM endpoint on a user's behalf.

    There is no universal system key — each preset / endpoint configures its own
    ``api_key`` (``config.llm_api_key`` per turn, plus each fallback endpoint's
    key). So the seam is *per endpoint*: given the user, the key that endpoint would
    otherwise use (``current_key``), and the lane the call is on, return the
    credential to actually use.

    V1 (:class:`~workspace_app.tokens.service.PassthroughTokenService`) returns
    ``current_key`` with no headers, so external behaviour is identical for every
    preset. A later impl fetches the user's personal token / session cookie from an
    external system and returns THAT instead — swapped in by config with no
    re-plumbing.

    Resolved for every agent turn that knows whose behalf it runs on —
    ``AgentToolContext.speaker`` for an interactive turn, ``acting_user`` for a
    background one. Turns with neither, and the non-agent sub-LLMs (retrieval,
    embedding, VLM), keep their configured keys and never reach here.
    """

    @abc.abstractmethod
    async def get_credential(
        self, user_id: str, current_key: str | None, lane: CallLane
    ) -> LlmCredential:
        """Return the credential to use for this endpoint on *user_id*'s behalf.

        *current_key* is the key the endpoint would otherwise use; *lane* says
        whether a person is waiting on the answer.

        A passthrough impl returns ``LlmCredential(current_key)`` unchanged. A real
        impl returns the user's personal credential; if the user has none it must
        fall back to *current_key* or raise — it must NOT return an empty credential,
        which would strip auth from a provider that requires it.
        """
        ...
