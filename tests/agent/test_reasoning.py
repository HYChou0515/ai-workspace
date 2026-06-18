"""reasoning_off_kwargs — provider-aware "reasoning OFF" disable params.

Ollama uses litellm ``think=False``; everything else (vLLM / openai-compatible)
uses ``extra_body chat_template_kwargs enable_thinking=False``.
"""

import pytest

from workspace_app.agent.reasoning import is_ollama, reasoning_off_kwargs


@pytest.mark.parametrize(
    "model",
    ["ollama_chat/qwen3:14b", "ollama/qwen3:14b"],
)
def test_ollama_uses_think_false(model):
    assert reasoning_off_kwargs(model) == {"think": False}
    assert is_ollama(model) is True


@pytest.mark.parametrize(
    "model",
    ["openai/gpt-4o", "qwen3-32b", "hosted_vllm/qwen3", "vllm/qwen3:14b"],
)
def test_non_ollama_uses_enable_thinking_false(model):
    assert reasoning_off_kwargs(model) == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
    }
    assert is_ollama(model) is False
