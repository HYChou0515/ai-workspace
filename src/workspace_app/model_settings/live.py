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
from typing import Any, TypeVar

from msgspec import Struct

from ..config.schema import Settings
from .effective import effective_settings
from .store import SettingsStore

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: How long a process trusts the version string it last read.
VERSION_TTL_S = 1.0


class Snapshot(Struct, frozen=True):
    """One published version, built: the document the page published (its
    patch — keys stay apart, M6) and the typed settings made from it with the
    keys that version put in force."""

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
        self._boot = boot
        self._snapshot = Snapshot(version=None, document={}, settings=boot)
        self._checked_at: float | None = None
        self._polling = False
        self._derived: dict[str, tuple[Snapshot, Any]] = {}
        self._derive_lock = threading.Lock()

    def attach(self, store: SettingsStore) -> None:
        """Where the published versions are read from. The store's models are
        registered after `spec.apply`, later than the composition that hands
        this object to every consumer — until then they run on `boot`."""
        with self._lock:
            self._store = store
            self._checked_at = None

    @property
    def config_path(self) -> Path | None:
        """The config file the settings are built over — what the page checks a
        draft against, so a check and a build cannot disagree."""
        return self._config_path

    @property
    def env(self) -> Mapping[str, str]:
        return self._env

    @property
    def boot(self) -> Settings:
        """The deploy defaults: config.yaml over the bundled settings, before
        anything published — what the page shows as "default"."""
        return self._boot

    def settings(self) -> Settings:
        return self.snapshot().settings

    def document(self) -> dict[str, Any]:
        """The published patch — what app.json and profiles are patched with."""
        return self.snapshot().document

    def version(self) -> str | None:
        return self.snapshot().version

    def derive(self, key: str, make: Callable[[Settings], T]) -> T:
        """`make(settings)`, built once per published version: what a consumer
        builds from the settings (a catalog, failover chains) is rebuilt when an
        Apply lands and handed back unchanged otherwise."""
        snapshot = self.snapshot()
        with self._derive_lock:
            held = self._derived.get(key)
            if held is not None and held[0] is snapshot:
                return held[1]
        value = make(snapshot.settings)
        with self._derive_lock:
            self._derived[key] = (snapshot, value)
        return value

    def snapshot(self) -> Snapshot:
        if self._polling:  # P10: the poll keeps it current; a use reads memory only
            return self._snapshot
        with self._lock:
            now = self._clock()
            if self._checked_at is not None and now - self._checked_at < self._ttl:
                return self._snapshot
            self._checked_at = now
            return self._refresh_locked()

    def refresh(self) -> Snapshot:
        """Read the version now and rebuild if it changed."""
        with self._lock:
            return self._refresh_locked()

    def start(self, interval_s: float = VERSION_TTL_S) -> Callable[[], None]:
        """Read the version every `interval_s` on a thread of its own, so no
        request waits on the database (P10: one read measured ~17 ms on
        Postgres, and the request that crossed the window paid it on the event
        loop). Returns the function that stops it."""
        stop = threading.Event()

        def poll() -> None:
            while not stop.wait(interval_s):
                self.refresh()

        thread = threading.Thread(target=poll, name="model-settings-poll", daemon=True)
        self._polling = True
        thread.start()

        def halt() -> None:
            stop.set()
            thread.join()
            self._polling = False

        return halt

    def _refresh_locked(self) -> Snapshot:
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
        # The keys the last Apply put in force — never the staged ones (M20).
        document = self._store.published_patch()
        settings = effective_settings(
            document,
            config_path=self._config_path,
            env=self._env,
            keys=self._store.applied_key_values(),
        )
        snapshot = Snapshot(version=version, document=document, settings=settings)
        if self._on_build is not None:
            self._on_build(snapshot)
        return snapshot
