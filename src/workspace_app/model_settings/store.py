"""Where the settings page's changes live (docs/plan-model-settings.md P2).

Changes are a JSON Merge Patch (RFC 7396) over the deploy defaults — only what
differs is stored (M11), `null` hides a default (M12), and resetting a value is
removing it from the patch. Four records:

- the **draft**: one shared patch; saving it changes nothing running (M4);
- **versions**: each Apply publishes the draft as a version — its id, the
  patch, who, when and the previous version (M5);
- the **current** pointer: one small record holding the published version id,
  which every user of the settings compares before each use (M4);
- **keys**: secret values by setting path, written and never read back through
  any route (M6). Each carries a revision marker that goes into the version id,
  so changing a key changes the version — but history holds the marker, never
  the value.

Registered after `spec.apply`, like `_UserQuota`: none of this may be reachable
through generic CRUD routes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import uuid
from typing import Any

from msgspec import Struct
from specstar import SpecStar
from specstar.query import QB
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

_DRAFT_ID = "draft"
_CURRENT_ID = "current"


class _SettingsDraft(Struct):
    patch: dict[str, Any]
    by: str


class _SettingsVersion(Struct):
    """One Apply. `version` is the content id consumers compare; the record's
    own id is per event, so applying an old patch again (a rollback) is a new
    entry in the history with the old content id."""

    version: str
    patch: dict[str, Any]
    by: str
    applied_at: float
    previous: str | None
    keys_changed: list[str]


class _SettingsCurrent(Struct):
    version: str
    patch: dict[str, Any]
    key_revs: dict[str, str]


class _SettingsKey(Struct):
    path: str
    value: str
    rev: str
    by: str


class _SettingsAppliedKey(Struct):
    """A key as the last Apply put it in force. The key the page sets is
    staged (`_SettingsKey`) until then (M20) — a process built from the staged
    value would run a key nobody applied (review r1)."""

    path: str
    value: str
    rev: str


class Applied(Struct, frozen=True):
    """A version as the history shows it — never a key's value."""

    version: str
    patch: dict[str, Any]
    by: str
    applied_at: float
    previous: str | None
    keys_changed: list[str]


def register_settings_models(spec: SpecStar) -> None:
    """Idempotently register the models, post-`spec.apply` (no CRUD routes)."""
    for model in (
        _SettingsDraft,
        _SettingsVersion,
        _SettingsCurrent,
        _SettingsKey,
        _SettingsAppliedKey,
    ):
        with contextlib.suppress(ValueError):
            spec.add_model(model)


def _content_id(patch: dict[str, Any], key_revs: dict[str, str]) -> str:
    """The version consumers compare: the published patch and which revision of
    each key — so a changed key is a new version, while history never sees its
    value."""
    canonical = json.dumps([patch, key_revs], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _key_id(path: str) -> str:
    """Resource ids cannot carry every character a setting path can."""
    return hashlib.sha256(path.encode()).hexdigest()[:24]


class SettingsStore:
    """Synchronous reads and writes; async callers wrap them in a thread."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    # ── helpers ─────────────────────────────────────────────────────────────

    def _get(self, model: type[Struct], resource_id: str) -> Any:
        rm = self._spec.get_resource_manager(model)
        try:
            return rm.get(resource_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError, KeyError):
            return None

    def _put(self, model: type[Struct], resource_id: str, record: Struct) -> None:
        rm = self._spec.get_resource_manager(model)
        try:
            rm.modify(resource_id, record, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            rm.restore(resource_id)
            rm.modify(resource_id, record, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(record, resource_id=resource_id, status=RevisionStatus.draft)

    # ── draft ───────────────────────────────────────────────────────────────

    def draft(self) -> dict[str, Any]:
        record = self._get(_SettingsDraft, _DRAFT_ID)
        return {} if record is None else record.patch

    def save_draft(self, patch: dict[str, Any], *, by: str) -> None:
        self._put(_SettingsDraft, _DRAFT_ID, _SettingsDraft(patch=patch, by=by))

    # ── published ───────────────────────────────────────────────────────────

    def current_version(self) -> str | None:
        """The one value every user of the settings reads before each use."""
        record = self._get(_SettingsCurrent, _CURRENT_ID)
        return None if record is None else record.version

    def published_patch(self) -> dict[str, Any]:
        record = self._get(_SettingsCurrent, _CURRENT_ID)
        return {} if record is None else record.patch

    def publish(self, patch: dict[str, Any], *, by: str) -> Applied:
        """Make `patch` the published settings, with the keys as they are now.
        Validation is the caller's (P3): this only records."""
        current = self._get(_SettingsCurrent, _CURRENT_ID)
        revs = self._key_revs()
        changed = self.pending_keys()
        event = _SettingsVersion(
            version=_content_id(patch, revs),
            patch=patch,
            by=by,
            applied_at=time.time(),
            previous=None if current is None else current.version,
            keys_changed=changed,
        )
        event_id = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"  # sorts by time
        self._put(_SettingsVersion, event_id, event)
        # Keys before the pointer: a process that builds in between sees the old
        # version with the new keys and rebuilds once the pointer moves. The
        # other order could leave it on the old keys under the new version.
        self._apply_keys()
        self._put(
            _SettingsCurrent,
            _CURRENT_ID,
            _SettingsCurrent(version=event.version, patch=patch, key_revs=revs),
        )
        return _applied(event)

    def applied(self, version: str) -> Applied | None:
        """The newest Apply of `version` — any one ever made, not only the
        newest 50 `history` lists (rollback reaches all of them)."""
        events = [e for e in self._list_data(_SettingsVersion) if e.version == version]
        if not events:
            return None
        return _applied(max(events, key=lambda event: event.applied_at))

    def history(self, limit: int = 50) -> list[Applied]:
        """Applies, newest first."""
        events = [rev.data for rev in self._list(_SettingsVersion)]
        events.sort(key=lambda event: event.applied_at, reverse=True)
        return [_applied(event) for event in events[:limit]]

    # ── keys ────────────────────────────────────────────────────────────────

    def set_key(self, path: str, value: str, *, by: str) -> None:
        record = _SettingsKey(path=path, value=value, rev=uuid.uuid4().hex, by=by)
        self._put(_SettingsKey, _key_id(path), record)

    def clear_key(self, path: str, *, by: str) -> None:
        rm = self._spec.get_resource_manager(_SettingsKey)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError, KeyError):
            rm.delete(_key_id(path))

    def key_status(self) -> dict[str, str]:
        """Which keys are set, each shown by its last four characters only."""
        return {key.path: "…" + key.value[-4:] for key in self._keys()}

    def pending_keys(self) -> list[str]:
        """Keys set or cleared since the last Apply — in force only after the
        next one (M20), so the page can say what an Apply would switch."""
        current = self._get(_SettingsCurrent, _CURRENT_ID)
        before = {} if current is None else current.key_revs
        now = self._key_revs()
        return sorted(p for p in set(now) | set(before) if now.get(p) != before.get(p))

    def key_values(self) -> dict[str, str]:
        """The staged values — what the next Apply puts in force; for checking a
        draft and testing a connection, never for a route's answer."""
        return {key.path: key.value for key in self._keys()}

    def applied_key_values(self) -> dict[str, str]:
        """The values the last Apply put in force — what a process builds with."""
        return {key.path: key.value for key in self._list_data(_SettingsAppliedKey)}

    def _apply_keys(self) -> None:
        staged = {key.path: key for key in self._keys()}
        rm = self._spec.get_resource_manager(_SettingsAppliedKey)
        for applied in self._list_data(_SettingsAppliedKey):
            if applied.path not in staged:
                with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
                    rm.delete(_key_id(applied.path))
        for path, key in staged.items():
            self._put(
                _SettingsAppliedKey,
                _key_id(path),
                _SettingsAppliedKey(path=path, value=key.value, rev=key.rev),
            )

    def _key_revs(self) -> dict[str, str]:
        return {key.path: key.rev for key in self._keys()}

    def _keys(self) -> list[_SettingsKey]:
        return self._list_data(_SettingsKey)

    def _list_data(self, model: type[Struct]) -> list[Any]:
        return [rev.data for rev in self._list(model)]

    def _list(self, model: type[Struct]) -> list[Any]:
        # Soft-deleted rows come back from list_resources: without the filter a
        # cleared key would still be "set" (the trap _UserQuota documents).
        query = (QB.is_deleted() == False).build()  # noqa: E712
        return list(self._spec.get_resource_manager(model).list_resources(query))


def _applied(event: _SettingsVersion) -> Applied:
    return Applied(
        version=event.version,
        patch=event.patch,
        by=event.by,
        applied_at=event.applied_at,
        previous=event.previous,
        keys_changed=list(event.keys_changed),
    )
