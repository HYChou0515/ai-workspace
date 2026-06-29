"""Read-only replay loaders for the diagnostics replay routes (#51 P4, #54).

Collapses ``create_app``'s ``_load_turn`` / ``_load_doc`` closures into a single
injectable class so ``register_replay_routes`` can be wired from a module rather
than a ``create_app`` local. Replay must never create/mutate anything, so these
do their own lookups instead of reusing ``_conversation_for`` (which creates a
conversation for a fresh investigation).
"""

from __future__ import annotations

from typing import Any

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources import AgentConfig, Conversation
from ..resources.kb import KbChat, SourceDoc
from ..tooling.registry import PackageInfo
from .locator import ItemLocator


class ReplayLoaders:
    """The read-only turn / document loaders passed to ``register_replay_routes``."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        locator: ItemLocator,
        packages: list[PackageInfo] | None,
        default_kb_agent_config: AgentConfig,
    ) -> None:
        self._spec = spec
        self._locator = locator
        self._packages = packages
        self._default_kb_agent_config = default_kb_agent_config
        self._conv_rm = spec.get_resource_manager(Conversation)

    def load_turn(
        self, source: str, thread_id: str
    ) -> tuple[list[Any], AgentConfig, list[PackageInfo] | None, str | None] | None:
        if source == "rca":
            for r in self._conv_rm.list_resources((QB["item_id"] == thread_id).build()):
                data = r.data
                assert isinstance(data, Conversation)
                # #94: no fallback. If the item can't resolve a config (gone /
                # unregistered App), there's nothing to replay — report "no turn".
                config = self._locator.resolve_agent_config(thread_id)
                if config is None:
                    return None
                return (
                    list(data.messages),
                    config,
                    self._packages,
                    self._locator.profile_of(thread_id),
                )
            return None
        # kb — the per-message model picker isn't persisted on the
        # message, so replay probes the deploy's default KB agent.
        kb_rm = self._spec.get_resource_manager(KbChat)
        try:
            chat = kb_rm.get(thread_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(chat, KbChat)
        return list(chat.messages), self._default_kb_agent_config, None, None

    def load_doc(self, document_id: str) -> tuple[str, str, bytes] | None:
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        try:
            rev = doc_rm.get(document_id)
        except ResourceIDNotFoundError:
            return None
        doc = rev.data
        assert isinstance(doc, SourceDoc)
        raw = doc_rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        ct = doc.content.content_type
        mime = ct if isinstance(ct, str) else "application/octet-stream"
        return doc.path, mime, raw
