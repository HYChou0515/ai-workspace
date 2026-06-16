"""with_collection_guidance (#90) — fold a collection's per-wiki guidance into a
bundled wiki ``AgentConfig``.

The guidance is ADDITIVE, never a replacement: the bundled prompt (tool usage,
citation rules, step budget) stays the base, and the collection's text is
appended as a ``## Collection-specific guidance`` block. So an operator shapes a
wiki's domain/organisation (maintainer) or answering style (reader) without
having to re-encode — or risk breaking — the machinery. Shared by the
maintainer/unfolder (coordinator) and the reader (orchestrator).
"""

from __future__ import annotations

import msgspec

from ...resources import AgentConfig

_HEADING = "## Collection-specific guidance"


def with_collection_guidance(config: AgentConfig, guidance: str) -> AgentConfig:
    """Return ``config`` with ``guidance`` appended to its ``system_prompt`` as a
    Collection-specific block, leaving every other field untouched. Blank
    guidance is a no-op — the config is returned as-is, so an un-customised
    collection runs on the bundled prompt exactly as before."""
    if not guidance.strip():
        return config
    block = f"\n\n{_HEADING}\n{guidance}"
    return msgspec.structs.replace(config, system_prompt=config.system_prompt + block)
