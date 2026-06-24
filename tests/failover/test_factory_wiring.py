"""Factory wiring (#196): a preset's `fallbacks` turns the KB LLM / VLM into a
busy-aware FallbackLlm / FallbackVlm; without them, the plain single-model
implementation is returned unchanged (no failover machinery)."""

from __future__ import annotations

from workspace_app.config.loader import load
from workspace_app.factories import get_kb_llm, get_kb_vlm, get_kb_vlm_formatter
from workspace_app.failover.llm import FallbackLlm, FallbackVlm
from workspace_app.kb.llm import LitellmLlm
from workspace_app.kb.vlm import LitellmVlm


def _settings(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(body)
    return load(config_path=p, env={})


def test_retrieval_llm_without_fallbacks_is_plain_litellm(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    solo: { model: "ollama_chat/qwen3:14b" }
kb:
  retrieval_llm: { preset: solo }
""",
    )
    assert isinstance(get_kb_llm(settings), LitellmLlm)


def test_retrieval_llm_with_fallbacks_is_fallback_llm(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m1", fallbacks: [spare] }
    spare: { model: "m2" }
kb:
  retrieval_llm: { preset: primary }
""",
    )
    assert isinstance(get_kb_llm(settings), FallbackLlm)


def test_disabled_retrieval_llm_is_none(tmp_path):
    settings = _settings(tmp_path, "kb:\n  retrieval_llm: null\n")
    assert get_kb_llm(settings) is None
    assert get_kb_vlm_formatter(settings) is None


def test_vlm_without_fallbacks_is_plain_litellm_vlm(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    vlm: { model: "ollama_chat/qwen2.5vl:7b" }
kb:
  vlm_llm: { preset: vlm }
""",
    )
    assert isinstance(get_kb_vlm(settings), LitellmVlm)


def test_vlm_with_fallbacks_is_fallback_vlm(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    vlm: { model: "ollama_chat/qwen2.5vl:7b", fallbacks: [vlm2] }
    vlm2: { model: "ollama_chat/qwen2.5vl:3b" }
kb:
  vlm_llm: { preset: vlm }
""",
    )
    assert isinstance(get_kb_vlm(settings), FallbackVlm)


def test_disabled_vlm_is_none(tmp_path):
    settings = _settings(tmp_path, "kb:\n  vlm_llm: null\n")
    assert get_kb_vlm(settings) is None


def test_vlm_formatter_with_fallbacks_is_fallback_llm(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    fmt: { model: "m1", fallbacks: [fmt2] }
    fmt2: { model: "m2" }
kb:
  vlm_format_llm: { preset: fmt }
""",
    )
    assert isinstance(get_kb_vlm_formatter(settings), FallbackLlm)
