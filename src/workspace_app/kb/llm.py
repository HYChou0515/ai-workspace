"""Llm Protocol — a one-shot text completion, used by the retriever's
multi-query / HyDE / rerank steps and (later) the KB agent. Swappable: tests
inject a fake; production uses LiteLLM (local Ollama or hosted).
"""

from __future__ import annotations

from typing import Protocol


class Llm(Protocol):
    def complete(self, prompt: str) -> str: ...


class LitellmLlm:
    """Production Llm via LiteLLM (model string routes the provider)."""

    def __init__(self, model: str) -> None:
        self._model = model

    def complete(self, prompt: str) -> str:  # pragma: no cover — hits a live model
        import litellm

        resp = litellm.completion(
            model=self._model, messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content or ""
