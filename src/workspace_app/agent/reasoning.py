"""Provider-aware "reasoning OFF" kwargs.

The OpenAI-style ``reasoning_effort="none"`` only disables thinking on Ollama
(litellm maps it to the ollama ``think`` flag); on vLLM / openai-compatible
servers it is a no-op. So when the chosen reasoning level is the OFF signal
(``"none"``) we have to send the right provider-specific disable param:

  - **Ollama** (model id starts with ``ollama_chat/`` or ``ollama/``): litellm
    ``think=False``. (Verified locally on qwen3: ``think=False`` drives
    ``reasoning_content`` length → 0; the vLLM ``chat_template_kwargs`` route
    does NOT bite on Ollama.)
  - **Everything else** (treated as vLLM / openai-compatible): pass
    ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` (per the
    vLLM docs; the user confirmed their vLLM server supports reasoning-off).

This is a pure helper so every generative call site can splat it into its
completion kwargs (or, at the Agents-SDK seam, fold ``think`` into
``ModelSettings.extra_args`` and ``extra_body`` into ``ModelSettings.extra_body``).
"""

from __future__ import annotations

from typing import Any

_OLLAMA_PREFIXES = ("ollama_chat/", "ollama/")


def is_ollama(model: str) -> bool:
    """True when ``model`` routes through Ollama (litellm ``ollama``/``ollama_chat``)."""
    return model.startswith(_OLLAMA_PREFIXES)


def reasoning_off_kwargs(model: str) -> dict[str, Any]:
    """Completion kwargs that disable thinking for ``model``'s provider.

    Splat into a litellm ``completion``/``acompletion`` call. Ollama →
    ``{"think": False}``; everything else →
    ``{"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}``.
    """
    if is_ollama(model):
        return {"think": False}
    return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
