"""Per-user credential resolution for the external (LLM) system.

Each preset configures its own endpoint ``api_key`` — there is no universal system
key — so ``ITokenService`` resolves a credential *per endpoint*: given the user, the
key that endpoint would otherwise use and the :data:`CallLane` the call is on,
return the ``LlmCredential`` (api_key + extra headers) to actually use. V1
(``PassthroughTokenService``) returns the key unchanged with no headers
(behaviour-preserving); a real user-keyed source is swapped in later, optionally
behind a per-user TTL cache (``CachingTokenService``), without touching the callers.
"""

from .protocol import CallLane, ITokenService, LlmCredential
from .service import CachingTokenService, PassthroughTokenService

__all__ = [
    "CachingTokenService",
    "CallLane",
    "ITokenService",
    "LlmCredential",
    "PassthroughTokenService",
]
