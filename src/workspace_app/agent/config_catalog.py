"""AgentConfigCatalog — directory of the deploy's KB-facing agent configs
(`kb_chat`, `infer_modules`) plus the named-preset registry.

Per-App workspace agents resolve through `apps.catalog.AppCatalog` (app ◇
profile ◇ preset), NOT this catalog — the old `workspace_chat` picker /
`/agent-configs` route / per-investigation `resolve()` were removed in #89 P8.
What remains is the purposes the KB subsystem needs:

- ``kb_chat`` — the KB chatbot's agent configs (the KB chat picker).
- ``infer_modules`` — the step-classification sub-agent.

Construct it from ``config.catalog_build.build_catalog`` (keyword args), or
directly with ``by_purpose=`` / ``kb_chats=`` / ``infer_modules=`` in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..resources import AgentConfig

if TYPE_CHECKING:
    from ..config.schema import Preset

_List = list


class AgentConfigCatalog:
    """The deploy's KB-facing agent configs, keyed by purpose, plus the
    preset registry. Stateless beyond the declared lists — accessors return
    copies so callers can't mutate the catalog's declared order."""

    def __init__(
        self,
        *,
        kb_chat: AgentConfig | None = None,
        kb_chats: _List[AgentConfig] | None = None,
        infer_modules: _List[AgentConfig] | None = None,
        by_purpose: dict[str, _List[AgentConfig]] | None = None,
        presets: dict[str, Preset] | None = None,
        config_dir: Path | None = None,
    ) -> None:
        # B-flat unified storage: ONE dict keyed by purpose name. `kb_chats=`
        # / `infer_modules=` populate the corresponding purpose; `by_purpose=`
        # lets callers register arbitrary purposes without growing the ctor.
        self._by_purpose: dict[str, _List[AgentConfig]] = {}
        if by_purpose is not None:
            for purpose, configs in by_purpose.items():
                self._by_purpose[purpose] = list(configs)
        if kb_chats is not None:
            self._by_purpose["kb_chat"] = list(kb_chats)
        elif kb_chat is not None:
            self._by_purpose.setdefault("kb_chat", [kb_chat])
        if infer_modules is not None:
            self._by_purpose["infer_modules"] = list(infer_modules)
        self._presets: dict[str, Preset] = dict(presets or {})
        self._config_dir: Path | None = config_dir

    # ─── B-flat unified API ──────────────────────────────────────────
    def configs_for(self, purpose: str) -> _List[AgentConfig]:
        """All `AgentConfig`s registered for `purpose`, in declared
        order. Empty list when the purpose has no entries. Replaces
        per-purpose accessors like `list()` / `kb_chats()` / etc."""
        return list(self._by_purpose.get(purpose, []))

    def default_for(self, purpose: str) -> AgentConfig | None:
        """First `AgentConfig` for `purpose` (matches the FE picker's
        visible default for that flavour). `None` when the purpose
        has no entries."""
        entries = self._by_purpose.get(purpose)
        return entries[0] if entries else None

    def purposes(self) -> _List[str]:
        """Every purpose name with at least one registered config."""
        return [p for p, entries in self._by_purpose.items() if entries]

    # ─── Per-purpose accessors (thin wrappers over the unified API) ──
    # Convenience named methods over configs_for/default_for for the KB
    # purposes existing call sites use.

    def kb_chat(self) -> AgentConfig | None:
        """Default KB chat AgentConfig — first entry of `kb_chats()`."""
        return self.default_for("kb_chat")

    def kb_chats(self) -> _List[AgentConfig]:
        """All KB chat AgentConfigs in declared order."""
        return self.configs_for("kb_chat")

    def kb_chat_by_name(self, name: str) -> AgentConfig | None:
        """KB chat entry by `name` — `None` for unknown names."""
        for cfg in self.configs_for("kb_chat"):
            if cfg.name == name:
                return cfg
        return None

    def infer_modules(self) -> AgentConfig | None:
        """Default `infer_modules` sub-agent AgentConfig."""
        return self.default_for("infer_modules")

    def infer_modules_configs(self) -> _List[AgentConfig]:
        """All `infer_modules` sub-agent configs in declared order."""
        return self.configs_for("infer_modules")

    def presets(self) -> dict[str, Preset]:
        """The named-preset registry — consumed by `apps.catalog.AppCatalog`
        to resolve each App's profile against its chosen preset. Returned as a
        copy so callers can't mutate the catalog."""
        return dict(self._presets)
