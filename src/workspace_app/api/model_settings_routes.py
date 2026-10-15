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
    DOES_NOT_LOAD,
    config_dir_of,
    effective_settings,
    load_problem,
    problems,
    redact,
    secrets_written,
)
from ..model_settings.live import LiveSettings
from ..model_settings.page import (
    config_tree,
    defaults_document,
    key_lands,
    key_path_problem,
    key_slots,
    mask,
    try_connection,
)
from ..model_settings.store import Applied, KeysMoved, SettingsStore


class _Patch(BaseModel):
    patch: dict[str, Any] = {}


#: An Apply's 409: a sentence, not a problem — nothing in the draft is wrong;
#: the page reads it again and the person looks before applying.
_MOVED = (
    "the draft or the keys changed since this page read them — "
    "someone else saved; look at the changes before applying"
)


class _SaveDraft(BaseModel):
    patch: dict[str, Any] = {}
    #: The saved draft the edits started from. When given, Save refuses (409)
    #: if the stored draft is another — someone saved since; overwriting it
    #: unseen lost their work (final review 8). Apply is guarded the same way.
    based_on: dict[str, Any] | None = None


class _Problems(BaseModel):
    problems: list[str] = []


class _AppliedOut(BaseModel):
    version: str
    patch: dict[str, Any]
    by: str
    applied_at: float
    previous: str | None = None
    keys_changed: list[str] = []


class _KeySlot(BaseModel):
    """One place a key can go (`page.KeySlot`)."""

    path: str
    owner: str
    kind: str
    name: str = ""


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
    #: Which revision of every staged key the page read — Apply sends it back,
    #: so a key set or cleared since is a 409, not published unseen.
    key_revision: str = ""
    #: The whole boot config, secrets masked — the baseline a pasted config is
    #: diffed against, so sections the page does not edit drop out when
    #: unchanged (M23).
    boot_config: dict[str, Any] = {}
    #: Every place a key can go in the saved draft — picked, never typed (M22).
    key_slots: list[_KeySlot] = []


class _Apply(BaseModel):
    #: The draft the confirmation listed. When given, Apply refuses (409) if the
    #: stored draft is another — someone saved since the dialog opened.
    draft: dict[str, Any] | None = None
    #: The key changes it listed — refused (409) when others are pending now.
    pending_keys: list[str] | None = None
    #: The page's `key_revision` — refused (409) when any staged key changed
    #: since, value included (final review 5: paths alone let a clear through
    #: that the dialog listed as a set).
    key_revision: str | None = None


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

        def slots() -> list[_KeySlot]:
            # From the saved draft, so a preset it adds is a place too; a draft
            # that does not load offers the deploy's places.
            try:
                built = effective_settings(
                    store.draft(), config_path=config_path, env=env, keys=store.key_values()
                )
            except DOES_NOT_LOAD:
                built = live.boot
            return [
                _KeySlot(path=k.path, owner=k.owner, kind=k.kind, name=k.name)
                for k in key_slots(built)
            ]

        def read() -> _PageOut:
            return _PageOut(
                defaults=defaults_document(live.boot),
                draft=store.draft(),
                published=store.published_patch(),
                version=store.current_version(),
                keys=store.key_status(),
                pending_keys=store.pending_keys(),
                applied_keys=sorted(store.applied_key_values()),
                key_revision=store.key_revision(),
                key_slots=slots(),
                boot_config=mask(config_tree(live.boot)),
            )

        return await asyncio.to_thread(read)

    @app.put("/admin/model-settings/draft")
    async def save_draft(body: _SaveDraft) -> _Problems:
        me = _require_superuser()

        def save() -> _Problems:
            # Refused before it is stored: a secret in the draft would sit in the
            # database and come back to every superuser in GET (M6).
            written = secrets_written(body.patch)
            if written:
                raise HTTPException(status_code=422, detail={"problems": written})
            if body.based_on is not None and body.based_on != store.draft():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "the saved draft changed since you started editing — someone "
                        "else saved. Your edits are still here; look at theirs (Discard "
                        "edits shows them) or save again to replace them"
                    ),
                )
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
            changed_values = (
                body is not None
                and body.key_revision is not None
                and body.key_revision != store.key_revision()
            )
            if changed_draft or changed_keys or changed_values:
                # A sentence, not a problem: nothing in the draft is wrong — the
                # page reads it again and the person looks before applying.
                raise HTTPException(status_code=409, detail=_MOVED)
            found = _problems(draft)
            if found:
                raise HTTPException(status_code=422, detail={"problems": found})
            try:
                return store.publish(
                    draft, by=me, key_revision=None if body is None else body.key_revision
                )
            except KeysMoved:
                raise HTTPException(status_code=409, detail=_MOVED) from None

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
            except DOES_NOT_LOAD:
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
            except DOES_NOT_LOAD as exc:
                return _ConnectionOut(
                    ok=False,
                    error=redact(
                        f"the draft does not load: {load_problem(exc, config_path, env)}",
                        store.key_values(),
                    ),
                )
            result = try_connection(
                settings,
                body.preset,
                deploy=effective_settings({}, config_path=config_path, env=env),
                page_keys=set(store.key_values().values()),
                config_dir=config_dir_of(config_path, env),
            )
            return _ConnectionOut(
                ok=result.ok,
                model=result.model,
                ms=result.ms,
                error=redact(result.error, store.key_values()),
            )

        return await asyncio.to_thread(call)
