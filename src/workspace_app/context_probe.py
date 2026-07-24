"""Ask the endpoint what it can take (#624 P5).

vLLM's OpenAI-compatible server exposes ``POST /tokenize``, whose reply carries
both an exact token count and ``max_model_len``. For a self-hosted model that is
the *only* source of truth: no registry knows its name, and the local estimators
disagree with the real tokenizer (measured on one Chinese string — ours 33,
litellm's 58, the tokenizer's ~30-34). A number the endpoint states beats every
number we can compute about it.

``/tokenize`` is a vLLM extension, **not** part of the OpenAI-compatible spec —
Ollama's ``/v1/models`` carries no length field at all. So the path where this
returns nothing is the normal one, not the exceptional one, and it is built to
be silent and free: every failure (404, timeout, junk body, no endpoint) yields
``None``, and ``None`` simply hands the question back to the rest of the design,
which never depended on an answer.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Kept short on purpose: this is a startup nicety, not a dependency. A slow or
#: wrong endpoint must cost a moment, not a boot.
PROBE_TIMEOUT_S = 3.0


def probe_context_limit(
    *,
    base_url: str | None,
    model: str,
    client: Any = None,
    timeout: float = PROBE_TIMEOUT_S,
) -> int | None:
    """The endpoint's ``max_model_len``, or ``None`` when it does not say.

    ``None`` is an ordinary answer here — most endpoints are not vLLM. It is
    never an error and never raises: the ceiling then resolves the way it does
    without any probe (operator config, what we learn from the traffic, the
    model registry), and if none of those answer either we simply send the whole
    history rather than trimming on a guess.
    """
    if not base_url:
        return None
    url = f"{base_url.rstrip('/')}/tokenize"
    try:
        http = client if client is not None else _default_client(timeout)
        resp = http.post(url, json={"model": model, "prompt": "ping"})
        if getattr(resp, "status_code", 0) != 200:
            logger.debug("context probe: %s answered %s", url, getattr(resp, "status_code", "?"))
            return None
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 — a probe must never break anything
        logger.debug("context probe: %s unavailable (%s)", url, type(exc).__name__)
        return None
    if not isinstance(body, dict):
        return None
    value = body.get("max_model_len")
    if not isinstance(value, int) or value <= 0:
        return None
    logger.info("context probe: %s reports max_model_len=%d", url, value)
    return value


def _default_client(timeout: float) -> Any:
    """A throwaway httpx client. Imported locally so this module stays importable
    (and testable with a fake) wherever httpx is not wanted."""
    import httpx

    return httpx.Client(timeout=timeout)
