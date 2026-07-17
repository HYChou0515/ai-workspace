"""Per-user API token resolution for the external (LLM) system.

Each preset configures its own endpoint ``api_key`` — there is no universal
system key — so ``ITokenService`` resolves a token *per endpoint*: given the user
and the key that endpoint would otherwise use, return the key to actually use.
V1 (``PassthroughTokenService``) returns it unchanged (behaviour-preserving); a
real user-keyed source is swapped in later, optionally behind a per-user TTL
cache (``CachingTokenService``), without touching the callers.
"""

from .protocol import ITokenService
from .service import CachingTokenService, PassthroughTokenService

__all__ = ["CachingTokenService", "ITokenService", "PassthroughTokenService"]
