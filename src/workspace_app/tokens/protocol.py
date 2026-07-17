"""ITokenService — the seam between a user id and that user's API token for the
external (LLM) system."""

from __future__ import annotations

import abc


class ITokenService(abc.ABC):
    """Resolve the API token to use for one LLM endpoint on a user's behalf.

    There is no universal system key — each preset / endpoint configures its own
    ``api_key`` (``config.llm_api_key`` per turn, plus each fallback endpoint's
    key). So the seam is *per endpoint*: given the user and the key that endpoint
    would otherwise use (``current_key``), return the key to actually use.

    V1 (:class:`~workspace_app.tokens.service.PassthroughTokenService`) returns
    ``current_key`` unchanged, so external behaviour is identical for every
    preset. A later impl fetches the user's personal token from an external
    system and returns THAT instead — swapped in by config with no re-plumbing.

    Resolved at the interactive agent turn, where the user is known
    (``AgentToolContext.speaker``). User-less paths (background jobs, retrieval
    sub-LLMs, embedders) keep their configured keys and never reach here.
    """

    @abc.abstractmethod
    async def get_token(self, user_id: str, current_key: str | None) -> str | None:
        """Return the api_key to use for this endpoint on *user_id*'s behalf.

        *current_key* is the key the endpoint would otherwise use. The return value
        IS the api_key (same ``str | None`` convention as everywhere else):

        - a ``str`` — use this key;
        - ``None`` — use no explicit key, i.e. the provider's default (e.g. local
          Ollama needs none). ``None`` is NOT a "this user has no token" sentinel.

        A passthrough impl returns *current_key* unchanged. A real impl returns the
        user's personal token; if the user has none it must fall back to
        *current_key* or raise — it must NOT return ``None`` for a missing token,
        which would strip auth from a provider that requires a key.
        """
        ...
