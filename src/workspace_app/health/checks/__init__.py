"""Bundled sanity checks (#51 P2) — one capability probe per
LLM-backed feature. Wired by ``factories.get_check_registry``."""

from .agents import ToolCallCheck
from .embedders import EmbedderDimCheck
from .kb_llm import InsightExtractionCheck, RetrievalExpandCheck
from .vlm import VlmDescribeCheck

__all__ = [
    "EmbedderDimCheck",
    "InsightExtractionCheck",
    "RetrievalExpandCheck",
    "ToolCallCheck",
    "VlmDescribeCheck",
]
