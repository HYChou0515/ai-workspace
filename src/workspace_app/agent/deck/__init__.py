"""Designed-pptx build loop (issue #284).

Public surface for the `make_deck` tool: a bounded multimodal
generate→render→see→fix loop that drives an ``IVlm`` to produce a *designed*
``.pptx`` with pptxgenjs. See ``docs/plan-issue-284.md``.
"""

from .loop import CraftAssets, DeckIO, DeckRequest, DeckResult, build_deck

__all__ = ["CraftAssets", "DeckIO", "DeckRequest", "DeckResult", "build_deck"]
