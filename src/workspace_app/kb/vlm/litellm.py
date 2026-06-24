"""LitellmVlm — production IVlm via LiteLLM (issue #39).

Images travel as OpenAI-style multimodal content parts
(``image_url`` with a base64 data URI), which LiteLLM translates for
Ollama (qwen2.5-vl et al.) and hosted vision providers alike.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence

from .protocol import IVlm


class LitellmVlm(IVlm):
    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float | None = None,
        num_retries: int = 0,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        # See LitellmLlm: per-call timeout + num_retries=0 under FallbackVlm so
        # the failover loop owns retry-by-switching (#196 / #131).
        self._timeout = timeout
        self._num_retries = num_retries

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:  # pragma: no cover — live model
        import litellm

        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for raw, mime in images:
            b64 = base64.b64encode(raw).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        for chunk in litellm.completion(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            stream=True,
            api_base=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout,
            num_retries=self._num_retries,
        ):
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield reasoning, True
            if delta.content:
                yield delta.content, False
