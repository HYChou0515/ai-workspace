"""The model settings page's routes (docs/plan-model-settings.md P7).

Superuser only (M1); anyone else gets 404, as the other admin routes do — that
the page exists is not something to probe for. The page edits one document
(`model_settings.effective`) as a JSON Merge Patch over the deploy defaults:

- `GET  /admin/model-settings` — defaults, draft, what is in force, which keys are set
- `PUT  /admin/model-settings/draft` — save the draft; changes nothing running (M4)
- `POST /admin/model-settings/check` — what would stop a patch running (as you type, M9)
- `POST /admin/model-settings/apply` — check the draft, publish it as a version (M4, M8)
- `GET  /admin/model-settings/history` — the Applies, newest first (M5)
- `POST /admin/model-settings/rollback` — publish an earlier version again (M5)
- `PUT / DELETE /admin/model-settings/keys` — write-only keys (M6, M20)
- `POST /admin/model-settings/test-connection` — one call to a preset (M8)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..model_settings.effective import effective_settings, problems, with_keys
from ..model_settings.live import LiveSettings
from ..model_settings.page import defaults_document, key_path_problem, try_connection
from ..model_settings.store import Applied, SettingsStore


class _Patch(BaseModel):
    patch: dict[str, Any] = {}


class _Problems(BaseModel):
    problems: list[str] = []


class _AppliedOut(BaseModel):
    version: str
    patch: dict[str, Any]
    by: str
    applied_at: float
    previous: str | None = None
    keys_changed: list[str] = []


class _PageOut(BaseModel):
    """Everything the page renders, in one answer so its parts agree."""

    defaults: dict[str, Any]
    draft: dict[str, Any]
    published: dict[str, Any]
    version: str | None = None
    #: path -> "…" + last four characters; never a value (M6).
    keys: dict[str, str] = {}


class _Rollback(BaseModel):
    version: str


class _Key(BaseModel):
    path: str
    value: str


class _TestConnection(BaseModel):
    preset: str


class _ConnectionOut(BaseModel):
    ok: bool
    model: str = ""
    ms: int = 0
    reply: str = ""
    error: str = ""


def _out(applied: Applied) -> _AppliedOut:
    return _AppliedOut(
        version=applied.version,
        patch=applied.patch,
        by=applied.by,
        applied_at=applied.applied_at,
        previous=applied.previous,
        keys_changed=list(applied.keys_changed),
    )


def register_model_settings_routes(
    app: APIRouter,
    *,
    store: SettingsStore,
    live: LiveSettings,
    get_user_id: Callable[[], str],
    superusers: frozenset[str],
) -> None:
    config_path, env = live.config_path, live.env

    def _require_superuser() -> str:
        me = get_user_id()
        if me not in superusers:
            raise HTTPException(status_code=404, detail="not found")
        return me

    def _problems(patch: dict[str, Any]) -> list[str]:
        # Checked as it would run: with the keys in it, as `LiveSettings` builds it.
        return problems(patch, config_path=config_path, env=env, keys=store.key_values())

    def _publish(patch: dict[str, Any], by: str) -> Applied:
        found = _problems(patch)
        if found:
            raise HTTPException(status_code=422, detail={"problems": found})
        return store.publish(patch, by=by)

    @app.get("/admin/model-settings")
    async def page() -> _PageOut:
        _require_superuser()

        def read() -> _PageOut:
            return _PageOut(
                defaults=defaults_document(live.boot),
                draft=store.draft(),
                published=store.published_patch(),
                version=store.current_version(),
                keys=store.key_status(),
            )

        return await asyncio.to_thread(read)

    @app.put("/admin/model-settings/draft")
    async def save_draft(body: _Patch) -> _Problems:
        me = _require_superuser()

        def save() -> _Problems:
            store.save_draft(body.patch, by=me)
            return _Problems(problems=_problems(body.patch))

        return await asyncio.to_thread(save)

    @app.post("/admin/model-settings/check")
    async def check(body: _Patch) -> _Problems:
        _require_superuser()
        return _Problems(problems=await asyncio.to_thread(_problems, body.patch))

    @app.post("/admin/model-settings/apply")
    async def apply() -> _AppliedOut:
        me = _require_superuser()
        return _out(await asyncio.to_thread(lambda: _publish(store.draft(), me)))

    @app.get("/admin/model-settings/history")
    async def history() -> list[_AppliedOut]:
        _require_superuser()
        return [_out(applied) for applied in await asyncio.to_thread(store.history)]

    @app.post("/admin/model-settings/rollback")
    async def rollback(body: _Rollback) -> _AppliedOut:
        me = _require_superuser()

        def roll() -> Applied:
            earlier = next((a for a in store.history() if a.version == body.version), None)
            if earlier is None:
                raise HTTPException(status_code=404, detail="no such version")
            applied = _publish(earlier.patch, me)
            # The draft follows what is now in force, or the next Apply would
            # quietly re-publish what was just rolled back.
            store.save_draft(earlier.patch, by=me)
            return applied

        return _out(await asyncio.to_thread(roll))

    @app.put("/admin/model-settings/keys", status_code=204)
    async def set_key(body: _Key) -> None:
        me = _require_superuser()
        problem = key_path_problem(body.path)
        if problem is not None:
            raise HTTPException(status_code=422, detail={"problems": [problem]})
        await asyncio.to_thread(store.set_key, body.path, body.value, by=me)

    @app.delete("/admin/model-settings/keys", status_code=204)
    async def clear_key(path: str) -> None:
        me = _require_superuser()
        await asyncio.to_thread(store.clear_key, path, by=me)

    @app.post("/admin/model-settings/test-connection")
    async def test_connection(body: _TestConnection) -> _ConnectionOut:
        _require_superuser()

        def call() -> _ConnectionOut:
            document = with_keys(store.draft(), store.key_values())
            try:
                settings = effective_settings(document, config_path=config_path, env=env)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                return _ConnectionOut(ok=False, error=f"the draft does not load: {exc}")
            result = try_connection(settings, body.preset)
            return _ConnectionOut(
                ok=result.ok,
                model=result.model,
                ms=result.ms,
                reply=result.reply,
                error=result.error,
            )

        return await asyncio.to_thread(call)
