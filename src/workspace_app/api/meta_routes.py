"""Platform meta routes (#54) — the signed-in user, the user directory, the App
launcher catalog, and the activity / telemetry feeds. Self-contained: they read
only the user directory, the activity log, and the telemetry monitor, so they lift
cleanly out of ``create_app``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from importlib import resources

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from ..monitor import IMonitor
from ..users import UserDirectory
from .activity import ActivityLog


def register_meta_routes(
    app: FastAPI | APIRouter,
    *,
    users: UserDirectory,
    get_user_id: Callable[[], str],
    activity: ActivityLog,
    monitor: IMonitor,
) -> None:
    """Mount the platform meta routes onto ``app``."""

    @app.get("/me")
    async def get_me() -> dict:
        """The signed-in user (resolved from the auth seam via the directory)."""
        return users.get(get_user_id()).to_dict()

    @app.get("/users")
    async def list_users() -> list[dict]:
        """The user directory — small enough to fetch whole and filter on the FE
        (mention / share pickers).

        Deduped by id (#42): a real directory may list a person once per
        section/group, and a repeated id becomes a repeated React key in the FE
        picker — which breaks its filtered rendering (stale rows linger, matches
        append at the bottom, the person shows 2-4×). First occurrence wins."""
        seen: set[str] = set()
        out: list[dict] = []
        for u in users.all_users():
            if u.id not in seen:
                seen.add(u.id)
                out.append(u.to_dict())
        return out

    @app.get("/apps")
    async def list_apps() -> list[dict]:
        """#89 P4a — launcher card summaries, one per registered App."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest

        out: list[dict] = []
        for slug in discover_app_slugs():
            m = load_app_manifest(slug)
            out.append(
                {
                    "slug": m.slug,
                    "title": m.title,
                    "description": m.description,
                    "icon": m.icon,
                    "color": m.color,
                }
            )
        return out

    @app.get("/apps/{slug}")
    async def get_app_manifest(slug: str) -> dict:
        """#89 P4a — the full App manifest the dashboard + workspace drive off.
        A shipped ``icon.svg`` is inlined so the FE gets it in one fetch."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.profiles import list_profiles, load_profile
        from ..apps.registry import app_model, resource_route
        from ..apps.schema import project_fields

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        m = load_app_manifest(slug)
        data = msgspec.to_builtins(m)
        data["resource_route"] = resource_route(slug)
        # The FE renders + inline-edits domain fields off this schema (kind +
        # enum options), projected from the model — never restated on the FE.
        data["fields"] = msgspec.to_builtins(project_fields(app_model(slug)))
        # The create flow's profile picker (#89 T1b): name + display strings per
        # profile, so the FE offers a choice when the App ships more than one.
        app_profiles = []
        for n in list_profiles(slug):
            p = load_profile(slug, n)
            app_profiles.append(
                {
                    "name": n,
                    "title": p.title or n,
                    "description": p.description,
                    # #198: the folder a chat attach stages files into; the FE
                    # resolves the active item's profile → this.
                    "upload_dir": p.upload_dir,
                }
            )
        data["profiles"] = app_profiles
        if m.icon.endswith(".svg"):
            with contextlib.suppress(FileNotFoundError, IsADirectoryError, OSError):
                data["icon"] = (resources.files("workspace_app.apps") / slug / m.icon).read_text(
                    "utf-8"
                )
        return data

    @app.get("/activity")
    async def get_activity() -> list[dict]:
        """Recent activity feed (newest first) for the notifications popover."""
        return activity.entries()

    @app.get("/monitor")
    async def get_monitor(limit: int | None = None, group_id: str | None = None) -> list[dict]:
        """Recent LLM/agent telemetry events (from the SDK trace stream),
        optionally scoped to one investigation via `group_id`."""
        return monitor.recent(limit=limit, group_id=group_id)

    @app.get("/monitor/stream")
    async def stream_monitor(group_id: str | None = None) -> StreamingResponse:
        """Live SSE feed of telemetry events as the SDK emits them."""
        return StreamingResponse(monitor.sse(group_id=group_id), media_type="text/event-stream")
