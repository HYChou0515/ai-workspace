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

from ..model_settings.effective import (
    effective_settings,
    problems,
    redact,
    secrets_written,
)
from ..model_settings.live import LiveSettings
from ..model_settings.page import (
    defaults_document,
    key_lands,
    key_path_problem,
    try_connection,
)
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
    #: path -> "…" + the last four characters of a long key, "" for a short
    #: one (shown as "set"); never a value (M6).
    keys: dict[str, str] = {}
    #: Keys set or cleared since the last Apply — what the next Apply switches (M20).
    pending_keys: list[str] = []
    #: Keys in force now — the last Apply's. Clearing one of these stops it at
    #: the next Apply; clearing any other only withdraws a staged change.
    applied_keys: list[str] = []


class _Apply(BaseModel):
    #: The draft the confirmation listed. When given, Apply refuses (409) if the
    #: stored draft is another — someone saved since the dialog opened.
    draft: dict[str, Any] | None = None
    #: The key changes it listed — refused (409) when others are pending now.
    pending_keys: list[str] | None = None


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

    def _problems(patch: dict[str, Any], keys: dict[str, str] | None = None) -> list[str]:
        # Checked as it would run: with the keys it would run with (by default
        # the staged ones, which the next Apply puts in force).
        found = problems(
            patch,
            config_path=config_path,
            env=env,
            keys=store.key_values() if keys is None else keys,
        )
        return found

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
                pending_keys=store.pending_keys(),
                applied_keys=sorted(store.applied_key_values()),
            )

        return await asyncio.to_thread(read)

    @app.put("/admin/model-settings/draft")
    async def save_draft(body: _Patch) -> _Problems:
        me = _require_superuser()

        def save() -> _Problems:
            # Refused before it is stored: a secret in the draft would sit in the
            # database and come back to every superuser in GET (M6).
            written = secrets_written(body.patch)
            if written:
                raise HTTPException(status_code=422, detail={"problems": written})
            store.save_draft(body.patch, by=me)
            return _Problems(problems=_problems(body.patch))

        return await asyncio.to_thread(save)

    @app.post("/admin/model-settings/check")
    async def check(body: _Patch) -> _Problems:
        _require_superuser()
        return _Problems(problems=await asyncio.to_thread(_problems, body.patch))

    @app.post("/admin/model-settings/apply")
    async def apply(body: _Apply | None = None) -> _AppliedOut:
        me = _require_superuser()

        def publish() -> Applied:
            draft = store.draft()
            changed_draft = body is not None and body.draft is not None and body.draft != draft
            changed_keys = (
                body is not None
                and body.pending_keys is not None
                and sorted(body.pending_keys) != store.pending_keys()
            )
            if changed_draft or changed_keys:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "problems": [
                            "the draft or the key changes moved since this page read "
                            "them — someone saved others; reload to see them before applying"
                        ]
                    },
                )
            found = _problems(draft)
            if found:
                raise HTTPException(status_code=422, detail={"problems": found})
            return store.publish(draft, by=me)

        return _out(await asyncio.to_thread(publish))

    @app.get("/admin/model-settings/history")
    async def history() -> list[_AppliedOut]:
        _require_superuser()
        return [_out(applied) for applied in await asyncio.to_thread(store.history)]

    @app.post("/admin/model-settings/rollback")
    async def rollback(body: _Rollback) -> _AppliedOut:
        me = _require_superuser()

        def roll() -> Applied:
            earlier = store.applied(body.version)
            if earlier is None:
                raise HTTPException(status_code=404, detail="no such version")
            # Checked with, and published with, the keys IN FORCE: a rollback
            # brings back values and leaves a staged key staged (review r2).
            found = _problems(earlier.patch, store.applied_key_values())
            if found:
                raise HTTPException(status_code=422, detail={"problems": found})
            applied = store.publish(earlier.patch, by=me, apply_keys=False)
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

        def owner_problem() -> str | None:
            # A key whose preset or role the saved draft does not have would be
            # accepted, listed, applied and used by nothing (final review).
            keys = {**store.key_values(), body.path: body.value}
            try:
                settings = effective_settings(
                    store.draft(), config_path=config_path, env=env, keys=keys
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                return f"{body.path}: the saved draft does not load — fix it on the Edit tab first"
            if key_lands(settings, body.path, body.value):
                return None
            return (
                f"{body.path}: no preset or role there in the saved draft — the key "
                "would do nothing (a typo?)"
            )

        problem = await asyncio.to_thread(owner_problem)
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
            try:
                settings = effective_settings(
                    store.draft(), config_path=config_path, env=env, keys=store.key_values()
                )
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                return _ConnectionOut(
                    ok=False,
                    error=redact(f"the draft does not load: {exc}", store.key_values()),
                )
            result = try_connection(
                settings,
                body.preset,
                deploy=effective_settings({}, config_path=config_path, env=env),
                page_keys=set(store.key_values().values()),
            )
            return _ConnectionOut(
                ok=result.ok,
                model=result.model,
                ms=result.ms,
                reply=result.reply,
                error=redact(result.error, store.key_values()),
            )

        return await asyncio.to_thread(call)
