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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Kept short on purpose: this is a startup nicety, not a dependency. A slow or
#: wrong endpoint must cost a moment, not a boot.
PROBE_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class EndpointLimits:
    """What a litellm proxy says it was configured with for one model.

    Two numbers, kept apart because they mean different things and only one of
    them is an answer to our question. `max_input_tokens` IS the input window.
    `max_tokens` is the OUTPUT cap for most entries — measured against litellm's
    own registry, gpt-4o reports 16,384 beside a 128,000 window — so it is
    returned raw and left to the caller to decide what, if anything, to make of
    it. Interpreting it here would bury a guess inside something that reads like
    a measurement.
    """

    max_input_tokens: int | None
    max_tokens: int | None


def probe_endpoint_limits(
    *,
    base_url: str | None,
    model: str,
    client: Any = None,
    timeout: float = PROBE_TIMEOUT_S,
) -> EndpointLimits | None:
    """What the endpoint's own model list says about `model`, or `None`.

    `/tokenize` asks the thing that ENFORCES the window, which is the best
    source there is — and it is a vLLM extension, so it does not survive a
    proxy. A deployment running self-hosted vLLM behind a litellm proxy
    therefore has no rung at all: the request reaches the proxy, which has no
    such route, and the model's name is a local alias no registry knows.

    litellm's management route answers from the model list it was configured
    with. That is a relayed declaration rather than the enforcer speaking, so it
    is ranked below `/tokenize` and below what the traffic taught us — but it is
    the only thing that answers at all in that topology.

    Silent on every failure, like the probe beside it: most endpoints are not a
    litellm proxy, and a 404 here is the ordinary case rather than a fault."""
    if not base_url:
        return None
    root = base_url.rstrip("/")
    # Both spellings, DEDUPED: `base_url` usually ends in `/v1` (the chat route
    # lives there) but need not, and litellm mounts the management route at both
    # `/model/info` and `/v1/model/info`. Deriving one variant by stripping and
    # the other by appending covers either way the operator wrote it — while
    # only stripping meant a url that did NOT end in `/v1` produced the same
    # address twice, so the most common path (not a litellm proxy at all) paid
    # two round trips and two timeouts to learn the same nothing, and the
    # `/v1/model/info` spelling was never reached.
    stripped = root.removesuffix("/v1")
    urls = _unique(f"{root}/model/info", f"{stripped}/model/info", f"{stripped}/v1/model/info")
    with _http(client, timeout) as http:
        for url in urls:
            found = _read_model_info(url, model=model, client=http, timeout=timeout)
            if found is not None:
                return found
    return None


def _unique(*urls: str) -> list[str]:
    """Order-preserving dedupe — the variants collapse to one or two depending
    on how the url was written, and asking the same address twice is waste
    charged to the path that learns nothing."""
    return list(dict.fromkeys(urls))


@contextmanager
def _http(client: Any, timeout: float) -> Iterator[Any]:
    """Close what we opened; never close what we were handed.

    A probe that opens a connection pool per request and drops it leaks the
    socket until the GC happens to run — and this one runs per (model, endpoint)
    on every pod, most often down the path that learns nothing at all. A caller
    that lends us a client keeps ownership of it."""
    if client is not None:
        yield client
        return
    http = _default_client(timeout)
    try:
        yield http
    finally:
        closer = getattr(http, "close", None)
        if callable(closer):
            closer()


def _read_model_info(url: str, *, model: str, client: Any, timeout: float) -> EndpointLimits | None:
    try:
        resp = client.get(url)
        if getattr(resp, "status_code", 0) != 200:
            logger.debug("context probe: %s answered %s", url, getattr(resp, "status_code", "?"))
            return None
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 — a probe must never break anything
        logger.debug("context probe: %s unavailable (%s)", url, type(exc).__name__)
        return None
    if not isinstance(body, dict):
        return None
    rows = body.get("data")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("model_name") != model:
            continue
        info = row.get("model_info")
        if not isinstance(info, dict):
            return None
        try:
            return EndpointLimits(
                max_input_tokens=_as_count(info.get("max_input_tokens")),
                max_tokens=_as_count(info.get("max_tokens")),
            )
        except (OverflowError, ValueError):  # a body carrying Infinity / NaN
            logger.debug("context probe: %s reported an unusable count", url)
            return None
    return None


def _as_count(value: Any) -> int | None:
    """A positive whole number, or None.

    Floats are accepted because litellm's own documented example reports
    `16385.0`, and rejecting that on a technicality would drop a real answer.
    The positivity check is applied AFTER truncation, or a fractional value
    below one (`0.5`) survives as `0` — a number that is neither a count nor
    absent, and which the field would then carry as if it were an answer."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    count = int(value)  # may raise on inf/nan; the caller treats that as silence
    return count if count > 0 else None


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
        with _http(client, timeout) as http:
            resp = http.post(url, json={"model": model, "prompt": "ping"})
            if getattr(resp, "status_code", 0) != 200:
                logger.debug(
                    "context probe: %s answered %s", url, getattr(resp, "status_code", "?")
                )
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
