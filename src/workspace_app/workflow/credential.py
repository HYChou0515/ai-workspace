"""Run-scoped credentials for capability calls (#100, manual §15).

A deterministic node's sandbox script reaches platform capabilities (ingest, …)
over HTTP. It authenticates with an **ephemeral, run-scoped credential**: a token
minted at run start, injected into the sandbox env, that maps to the captured user,
is scoped to that run, and **expires** (so a leaked token can't be replayed later).
The orchestrator mints + revokes; the capability endpoint resolves.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Claims:
    """What a valid token maps to: the run + the captured user it acts as, the item
    it is scoped to, and when it expires (epoch ms)."""

    run_id: str
    user: str
    item_id: str
    expires_at: int


@dataclass
class CredentialBroker:
    """Mints, validates, and revokes run-scoped tokens (manual §15). In-memory — a
    token is valid only on the pod that minted it (a run lives on one pod)."""

    now: Callable[[], int] = _now_ms
    _claims: dict[str, Claims] = field(default_factory=dict)

    def mint(self, *, run_id: str, user: str, item_id: str, ttl_ms: int) -> str:
        """A fresh opaque token bound to (run, user, item), expiring after ``ttl_ms``."""
        token = secrets.token_urlsafe(24)
        self._claims[token] = Claims(
            run_id=run_id, user=user, item_id=item_id, expires_at=self.now() + ttl_ms
        )
        return token

    def resolve(self, token: str) -> Claims | None:
        """The token's claims, or ``None`` if unknown or expired (expired tokens are
        dropped on read)."""
        claims = self._claims.get(token)
        if claims is None:
            return None
        if self.now() >= claims.expires_at:
            self._claims.pop(token, None)
            return None
        return claims

    def revoke(self, run_id: str) -> None:
        """Drop every token for a run — called when the run ends (manual §15)."""
        for token in [t for t, c in self._claims.items() if c.run_id == run_id]:
            self._claims.pop(token, None)
