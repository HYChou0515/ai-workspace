"""The consumers' side of live settings (docs/plan-model-settings.md P5).

What the API and the workers used to build once from the boot `Settings` — the
agent-config catalog (KB chat list, sub-agent defaults, the preset registry),
the App catalog — built instead from `LiveSettings`, once per published version
(`LiveSettings.derive`), so the next use after an Apply sees it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from specstar import SpecStar

from ..agent.config_catalog import AgentConfigCatalog
from ..apps.overlay import set_source as set_app_overlay_source
from ..config.catalog_build import build_catalog
from ..config.schema import Preset, Settings
from ..kb.llm import ILlm
from ..kb.vlm.protocol import IVlm
from ..resources import AgentConfig
from .live import LiveSettings
from .store import SettingsStore, register_settings_models


class _LiveAgentConfigCatalog(AgentConfigCatalog):
    """`AgentConfigCatalog` whose lists and presets are those of the current
    published version. Every accessor of the base class reads `_by_purpose` /
    `_presets`, so overriding those two is the whole change."""

    def __init__(self, live: LiveSettings, config_dir: Path | None) -> None:
        self._live = live
        self._config_dir = config_dir

    def _built(self) -> AgentConfigCatalog:
        config_dir = self._config_dir
        return self._live.derive(
            "agent-config-catalog", lambda s: build_catalog(s, config_dir=config_dir)
        )

    @property
    def _by_purpose(self) -> dict[str, list[AgentConfig]]:
        return self._built()._by_purpose

    @property
    def _presets(self) -> dict[str, Preset]:
        return self._built()._presets


class RoleOff(RuntimeError):
    """A role that was on at boot (so its feature was wired) and that the
    published settings have since turned off. Turning a role on or off changes
    which features exist, which happens at boot only — the call fails readably
    instead of running a model nobody configured."""


class LiveClient[C]:
    """The client for one role, rebuilt only when what it is built from — the
    role's resolved endpoint chain — changes (M14): an Apply that edits another
    preset keeps this client, and its connections, as they are."""

    def __init__(
        self, role: str, source: Callable[[], Any], make: Callable[[Any], C | None]
    ) -> None:
        self._role = role
        self._source = source
        self._make = make
        self._lock = threading.Lock()
        self._held: tuple[str, C] | None = None

    def current(self) -> C:
        built_from = self._source()
        fingerprint = repr(built_from)
        with self._lock:
            if self._held is not None and self._held[0] == fingerprint:
                return self._held[1]
        client = self._make(built_from)
        if client is None:
            raise RoleOff(
                f"{self._role} has been turned off in the model settings; "
                "a role is switched on or off by a restart"
            )
        with self._lock:
            self._held = (fingerprint, client)
        return client


class LiveLlm(ILlm):
    """An `ILlm` for a role whose endpoints come from the published settings on
    each call. Every receiver only calls `stream` / `collect`, so it stands in
    for the client the factory used to build once."""

    def __init__(self, client: LiveClient[ILlm]) -> None:
        self._client = client

    def current(self) -> ILlm:
        return self._client.current()

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        return self.current().stream(prompt)


class LiveVlm(IVlm):
    """`LiveLlm` for a vision role."""

    def __init__(self, client: LiveClient[IVlm]) -> None:
        self._client = client

    def current(self) -> IVlm:
        return self._client.current()

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        return self.current().stream(prompt, images=images)


def live_llm(
    live: LiveSettings,
    role: str,
    chain_of: Callable[[Settings], Sequence[Any]],
    make: Callable[[Any], ILlm | None],
) -> LiveLlm:
    return LiveLlm(LiveClient(role, lambda: list(live.derive(f"chain:{role}", chain_of)), make))


def live_vlm(
    live: LiveSettings,
    role: str,
    chain_of: Callable[[Settings], Sequence[Any]],
    make: Callable[[Any], IVlm | None],
) -> LiveVlm:
    return LiveVlm(LiveClient(role, lambda: list(live.derive(f"chain:{role}", chain_of)), make))


def live_agent_config_catalog(
    live: LiveSettings, config_dir: Path | None = None
) -> AgentConfigCatalog:
    return _LiveAgentConfigCatalog(live, config_dir)


def follow_published(live: LiveSettings, spec: SpecStar) -> SettingsStore:
    """Make this process follow what the settings page publishes: the store's
    models registered (post-`spec.apply`, no CRUD routes), `live` reading from
    it, and every app.json / profile reader seeing the published patch. The API
    (`create_app`) and the workers (`worker.build_bundle`) both call this — one
    sequence, so the two cannot drift."""
    register_settings_models(spec)
    store = SettingsStore(spec)
    live.attach(store)

    def published() -> tuple[str | None, dict[str, Any]]:
        snapshot = live.snapshot()  # once: version and document agree
        return snapshot.version, snapshot.document

    set_app_overlay_source(published)
    return store


def published_settings(
    settings: Settings, *, config_path: Path | None, env: Mapping[str, str], spec: SpecStar
) -> Settings:
    """For a one-shot tool (`skill_eval`): the settings the page last published
    over `settings`, and app.json / profile readers following them too — so the
    turn it measures is the turn production runs (M13)."""
    live = LiveSettings(None, config_path=config_path, env=env, boot=settings)
    follow_published(live, spec)
    return live.settings()
