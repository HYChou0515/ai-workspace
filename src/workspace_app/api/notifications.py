"""Per-user notifications: the `notify()` producer helper + the recipient's
read/list routes. Producers (status change, chat share, @mention) call
`notify(...)`; the bell polls `GET /notifications`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import msgspec
from fastapi import FastAPI, HTTPException, Response
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources import Notification


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def notify(
    spec: SpecStar,
    *,
    recipient: str,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
    actor: str | None = None,
) -> str:
    """Create one notification addressed to `recipient`. Returns its id."""
    rm = spec.get_resource_manager(Notification)
    rev = rm.create(
        Notification(
            recipient=recipient,
            kind=kind,
            title=title,
            body=body,
            link=link,
            actor=actor,
            created_at=_now_ms(),
        )
    )
    return rev.resource_id


def _to_dict(resource_id: str, n: Notification) -> dict:
    return {
        "resource_id": resource_id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "actor": n.actor,
        "read": n.read,
        "created_at": n.created_at,
    }


def register_notification_routes(
    app: FastAPI, spec: SpecStar, get_user_id: Callable[[], str]
) -> None:
    rm = spec.get_resource_manager(Notification)

    def _mine(me: str) -> list[tuple[str, Notification]]:
        # Indexed query by recipient (indexed in register_all) — not a scan.
        out: list[tuple[str, Notification]] = []
        for r in rm.list_resources((QB["recipient"] == me).build()):
            d = r.data
            assert isinstance(d, Notification)
            out.append((r.info.resource_id, d))  # ty: ignore[unresolved-attribute]
        return out

    @app.get("/notifications")
    async def list_notifications() -> list[dict]:
        """The current user's notifications, most recent first."""
        rows = _mine(get_user_id())
        rows.sort(key=lambda rd: rd[1].created_at or 0, reverse=True)
        return [_to_dict(rid, d) for rid, d in rows]

    @app.post("/notifications/read-all", status_code=204)
    async def mark_all_read() -> Response:
        for rid, data in _mine(get_user_id()):
            if not data.read:
                rm.update(rid, msgspec.structs.replace(data, read=True))
        return Response(status_code=204)

    @app.post("/notifications/{notification_id}/read", status_code=204)
    async def mark_read(notification_id: str) -> Response:
        try:
            data = rm.get(notification_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(data, Notification)
        if data.recipient != get_user_id():
            raise HTTPException(status_code=403, detail="not your notification")
        if not data.read:
            rm.update(notification_id, msgspec.structs.replace(data, read=True))
        return Response(status_code=204)
