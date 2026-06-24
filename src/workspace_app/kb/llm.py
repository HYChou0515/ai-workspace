"""ILlm — a **streaming** text completion. The retriever's multi-query / HyDE /
rerank steps run through it, and because it always streams, their thinking can
be surfaced live in the chat (issue #10). Swappable: tests inject a fake;
production uses LiteLLM (local Ollama or hosted).

There is deliberately NO non-streaming `complete()` — a one-shot call would
hide the model's work and badly hurt observability. Callers that need the final
text use `collect()`, which still streams under the hood.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Iterator

# Live-progress sink for streamed chunks: (text, is_reasoning).
OnChunk = Callable[[str, bool], None]


class ILlm(abc.ABC):
    @abc.abstractmethod
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        """Yield ``(text_chunk, is_reasoning)`` as the model produces them —
        ``is_reasoning`` marks the model's thinking (e.g. qwen3 ``<think>``) vs
        its actual output. Always streaming; the only primitive. Should not
        raise for an empty/garbage result — yield what you have."""
        ...

    def collect(self, prompt: str, on_chunk: OnChunk | None = None) -> str:
        """Drain ``stream()``: forward every chunk to ``on_chunk`` (so the live
        thinking can be surfaced) and return the joined **non-reasoning**
        content for the caller to parse. Built on ``stream()`` — not a separate
        non-streaming call."""
        out: list[str] = []
        for text, reasoning in self.stream(prompt):
            if on_chunk is not None:
                on_chunk(text, reasoning)
            if not reasoning:
                out.append(text)
        return "".join(out)


class LitellmLlm(ILlm):
    """Production ILlm via LiteLLM (model string routes the provider)."""

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        *,
        timeout: float | None = None,
        num_retries: int = 0,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        # Factory-configured (kb.retrieval_llm.reasoning_effort). None ⇒ omit the
        # param (model default). "none" ⇒ Ollama think=False (no <think> on query
        # expansion / HyDE / rerank); low|medium|high ⇒ thinking on.
        self.reasoning_effort = reasoning_effort
        # Per-call timeout (so an abandoned producer thread eventually dies) and
        # num_retries. Under FallbackLlm these are set from the endpoint budget
        # with num_retries=0 — the failover loop owns retry-by-switching, not
        # litellm's blind in-place retry (#196).
        self._timeout = timeout
        self._num_retries = num_retries

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        import litellm

        from ..agent.reasoning import reasoning_off_kwargs

        # reasoning_effort: None ⇒ omit (model default); low|medium|high ⇒ pass
        # through (thinking on). "none" is the OFF signal — but the OpenAI
        # reasoning_effort="none" only disables thinking on Ollama, so instead
        # send the provider-correct disable param (Ollama think=False / others
        # vLLM enable_thinking=False) and drop the no-op reasoning_effort.
        if self.reasoning_effort is None:
            extra: dict[str, object] = {}
        elif self.reasoning_effort == "none":
            extra = reasoning_off_kwargs(self._model)
        else:
            extra = {"reasoning_effort": self.reasoning_effort}
        for chunk in litellm.completion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            api_base=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout,
            num_retries=self._num_retries,
            **extra,
        ):
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield reasoning, True
            if delta.content:
                yield delta.content, False
