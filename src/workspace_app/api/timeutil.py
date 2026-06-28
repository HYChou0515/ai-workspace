"""Epoch-millisecond helpers (#54), shared by the route modules + ``create_app``."""

from __future__ import annotations

from datetime import UTC, datetime


def now_ms() -> int:
    """Epoch milliseconds — stamped on persisted messages so the agent log's
    timestamps survive a reload (FE `Date` works in ms)."""
    return round(datetime.now(UTC).timestamp() * 1000)


def dt_ms(dt: datetime) -> int:
    """A specstar revision time (`updated_time`/`created_time`) → epoch ms."""
    return round(dt.timestamp() * 1000)
