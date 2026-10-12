"""What a process runs with (docs/plan-model-settings.md P4).

One `LiveSettings` per process — the API and every worker. Each use asks it for
the settings; it compares the published version string with the one it built
from and rebuilds only when they differ (M4). The string is read from the
database at most once per `version_ttl_s` (1 s): a single request reads the
settings many times, and one small read a second per process is the whole
cost. A failed read, or a published document that cannot build, keeps the
last good settings and is logged — a broken Apply must not take a running
process down with it (M14).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from msgspec import Struct

from ..config.schema import Settings
from .effective import effective_settings, with_keys
from .store import SettingsStore

logger = logging.getLogger(__name__)

#: How long a process trusts the version string it last read.
VERSION_TTL_S = 1.0


class Snapshot(Struct, frozen=True):
    """One published version, built: the document the page published (patch
    with keys) and the typed settings made from it."""

    version: str | None
    document: dict[str, Any]
    settings: Settings


class LiveSettings:
    def __init__(
        self,
        store: SettingsStore | None,
        *,
        config_path: Path | None,
        env: Mapping[str, str],
        boot: Settings,
        version_ttl_s: float = VERSION_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        on_build: Callable[[Snapshot], None] | None = None,
    ) -> None:
        self._store = store
        self._config_path = config_path
        self._env = env
        self._ttl = version_ttl_s
        self._clock = clock
        self._on_build = on_build
        self._lock = threading.Lock()
        self._snapshot = Snapshot(version=None, document={}, settings=boot)
        self._checked_at: float | None = None

    def settings(self) -> Settings:
        return self.snapshot().settings

    def document(self) -> dict[str, Any]:
        """The published document (patch with keys) — what app.json and
        profiles are patched with."""
        return self.snapshot().document

    def version(self) -> str | None:
        return self.snapshot().version

    def snapshot(self) -> Snapshot:
        with self._lock:
            now = self._clock()
            if self._checked_at is not None and now - self._checked_at < self._ttl:
                return self._snapshot
            self._checked_at = now
            if self._store is None:
                return self._snapshot
            try:
                version = self._store.current_version()
            except Exception:  # the database away: run on what we have
                logger.warning(
                    "model settings: could not read the version; keeping %s",
                    self._snapshot.version,
                    exc_info=True,
                )
                return self._snapshot
            if version == self._snapshot.version:
                return self._snapshot
            try:
                self._snapshot = self._build(version)
            except Exception:
                logger.error(
                    "model settings: version %s does not build; keeping %s",
                    version,
                    self._snapshot.version,
                    exc_info=True,
                )
            return self._snapshot

    def _build(self, version: str | None) -> Snapshot:
        assert self._store is not None
        document = with_keys(self._store.published_patch(), self._store.key_values())
        settings = effective_settings(document, config_path=self._config_path, env=self._env)
        snapshot = Snapshot(version=version, document=document, settings=settings)
        if self._on_build is not None:
            self._on_build(snapshot)
        return snapshot
