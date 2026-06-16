"""CheckRun — one persisted sanity-check result (#51).

Audit history of every probe execution (startup rounds + manual
re-runs). The LATEST result per check lives in HealthService's
in-memory cache (what `GET /health/checks` serves); these rows are
the trail an operator greps when asking "when did the VLM stop
passing?".
"""

from __future__ import annotations

from msgspec import Struct


class CheckRun(Struct):  # → resource "check-run"
    check_id: str
    status: str  # pass | fail | skip | error (validated upstream)
    detail: str = ""
    latency_ms: int = 0
    checked_at: int = 0  # epoch ms
