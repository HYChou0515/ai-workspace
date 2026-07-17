"""Per-user API token resolution for the external (LLM) system.

``ITokenService`` maps a user id to that user's token. V1 (``SystemTokenService``)
returns the system token for everyone — behaviour-preserving — behind an optional
per-user TTL cache (``CachingTokenService``); the source is swapped for a real
per-user impl later without touching the callers.
"""

from .protocol import ITokenService
from .service import CachingTokenService, SystemTokenService

__all__ = ["CachingTokenService", "ITokenService", "SystemTokenService"]
