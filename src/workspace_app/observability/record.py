"""Build a faithful, replayable record from one litellm callback payload
(observability feature B).

litellm's success/failure callbacks hand us `kwargs` (the exact request it
sent) + `response_obj` (the fully-assembled response, even for streamed
calls). We reshape that — losslessly — into:

- ``request``: a flat dict that is exactly ``litellm.completion(**request)``
  kwargs (provider-prefixed model rebuilt, full messages + tools + params,
  clean api_base, real api_key) so the operator can copy-paste it to re-fire.
- ``response``: the assembled response verbatim (content + tool_calls +
  finish_reason + usage).
- ``meta``: the human-scannable summary (outcome, latency, usage, the
  ``litellm_call_id`` correlation key).

Pure functions — no I/O, no litellm imports. The writer + logger wire these in.
"""

from __future__ import annotations

from typing import Any

# litellm `call_type` buckets. Generative calls get a full per-call record;
# embeddings/rerank get a one-line summary (no readable "reply"); anything
# else still gets a summary so nothing is silently dropped.
GENERATIVE_CALL_TYPES = frozenset({"completion", "acompletion"})
EMBEDDING_CALL_TYPES = frozenset({"embedding", "aembedding"})
RERANK_CALL_TYPES = frozenset({"rerank", "arerank"})


def classify_call(call_type: str | None) -> str:
    """`generative` | `embedding` | `rerank` | `other` — drives whether a call
    is logged in full or summarised."""
    if call_type in GENERATIVE_CALL_TYPES:
        return "generative"
    if call_type in EMBEDDING_CALL_TYPES:
        return "embedding"
    if call_type in RERANK_CALL_TYPES:
        return "rerank"
    return "other"


def build_call_record(
    kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
) -> dict[str, Any]:
    """Faithful record for one generative call. See module docstring."""
    response = _as_dict(response_obj)
    model = _full_model(kwargs)
    api_base = _clean_api_base(kwargs)
    api_key = _litellm_params(kwargs).get("api_key")
    optional_params = dict(kwargs.get("optional_params") or {})
    message = _first_message(response)
    return {
        "meta": {
            "ts": _iso(start_time),
            "call_type": kwargs.get("call_type"),
            "model": model,
            "endpoint": _host(api_base),
            "latency_ms": _latency_ms(start_time, end_time),
            "usage": response.get("usage"),
            "outcome": _outcome(message),
            "litellm_call_id": _hidden_params(kwargs).get("litellm_call_id"),
        },
        # Exactly litellm.completion(**request) kwargs — copy-paste replayable.
        "request": {
            "model": model,
            "messages": kwargs.get("messages"),
            **optional_params,
            "api_base": api_base,
            "api_key": api_key,
        },
        "response": response,
    }


def build_failure_record(
    kwargs: dict[str, Any], exception: Any, start_time: Any, end_time: Any
) -> dict[str, Any]:
    """Record for a call that FAILED — the same replayable `request` block plus
    the error (so the operator sees exactly what request errored, and can
    re-fire it). No `response`; `error` carries the traceback text."""
    model = _full_model(kwargs)
    api_base = _clean_api_base(kwargs)
    return {
        "meta": {
            "ts": _iso(start_time),
            "call_type": kwargs.get("call_type"),
            "model": model,
            "endpoint": _host(api_base),
            "latency_ms": _latency_ms(start_time, end_time),
            "outcome": "error",
            "litellm_call_id": _hidden_params(kwargs).get("litellm_call_id"),
        },
        "request": {
            "model": model,
            "messages": kwargs.get("messages"),
            **dict(kwargs.get("optional_params") or {}),
            "api_base": api_base,
            "api_key": _litellm_params(kwargs).get("api_key"),
        },
        "error": _error_text(exception),
    }


def _error_text(exception: Any) -> str:
    import traceback

    if isinstance(exception, BaseException):
        tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
        return "".join(tb).strip() or repr(exception)
    return str(exception)


def build_summary(
    kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
) -> dict[str, Any]:
    """One-line summary for a non-generative call (embedding / rerank / other)
    — records that it happened + its shape, without flooding the log with
    vectors. ``n`` = number of embedded/reranked inputs, ``dim`` = vector width
    when discoverable."""
    response = _as_dict(response_obj)
    data = response.get("data") or []
    dim = None
    if data and isinstance(data[0], dict):
        emb = data[0].get("embedding")
        if isinstance(emb, list):
            dim = len(emb)
    return {
        "ts": _iso(start_time),
        "call_type": kwargs.get("call_type"),
        "kind": classify_call(kwargs.get("call_type")),
        "model": _full_model(kwargs),
        "endpoint": _host(_clean_api_base(kwargs)),
        "n": _input_count(kwargs, data),
        "dim": dim,
        "latency_ms": _latency_ms(start_time, end_time),
        "litellm_call_id": _hidden_params(kwargs).get("litellm_call_id"),
    }


# ─── helpers ────────────────────────────────────────────────────────────


def _as_dict(response_obj: Any) -> dict[str, Any]:
    """Normalise a litellm response (pydantic ModelResponse / EmbeddingResponse,
    or already a dict) to a plain dict."""
    if isinstance(response_obj, dict):
        return response_obj
    dump = getattr(response_obj, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:  # noqa: BLE001 — fall through to dict() below
            pass
    try:
        return dict(response_obj)
    except Exception:  # noqa: BLE001 — opaque object; keep its repr, drop nothing silently
        return {"_unserializable": repr(response_obj)}


def _litellm_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    lp = kwargs.get("litellm_params")
    return lp if isinstance(lp, dict) else {}


def _hidden_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    meta = _litellm_params(kwargs).get("metadata")
    hp = meta.get("hidden_params") if isinstance(meta, dict) else None
    return hp if isinstance(hp, dict) else {}


def _full_model(kwargs: dict[str, Any]) -> str:
    """Reconstruct the provider-prefixed model litellm was called with —
    `kwargs['model']` has the provider stripped (`qwen3:8b`), so re-attach
    `custom_llm_provider` (`ollama_chat/qwen3:8b`) for a replayable value."""
    model = kwargs.get("model") or ""
    if "/" in model:
        return model
    provider = (
        kwargs.get("custom_llm_provider") or _hidden_params(kwargs).get("custom_llm_provider") or ""
    )
    return f"{provider}/{model}" if provider else model


def _clean_api_base(kwargs: dict[str, Any]) -> str | None:
    """The endpoint base for replay — prefer the clean one from hidden_params
    (`http://host:11434`) over `litellm_params.api_base`, which providers may
    have suffixed (`.../api/chat`) in a way that double-appends on replay."""
    hidden = _hidden_params(kwargs).get("api_base")
    if hidden:
        return hidden
    return _litellm_params(kwargs).get("api_base")


def _first_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict):
            return msg
    return {}


def _outcome(message: dict[str, Any]) -> str:
    """What the model did: `tool_call` (emitted ≥1 tool call), `text` (prose),
    or `empty` (nothing user-visible)."""
    if message.get("tool_calls"):
        return "tool_call"
    if message.get("content"):
        return "text"
    return "empty"


def _input_count(kwargs: dict[str, Any], data: list[Any]) -> int | None:
    inp = kwargs.get("input")
    if isinstance(inp, list):
        return len(inp)
    if isinstance(inp, str):
        return 1
    return len(data) or None


def _latency_ms(start_time: Any, end_time: Any) -> int | None:
    try:
        return round((end_time - start_time).total_seconds() * 1000)
    except Exception:  # noqa: BLE001 — non-datetime start/end; latency simply unknown
        return None


def _iso(value: Any) -> str:
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _host(api_base: str | None) -> str:
    """`host:port` of an endpoint — scheme/path/credentials dropped (the
    api_key field carries creds; the endpoint should not)."""
    if not api_base:
        return "default"
    from urllib.parse import urlsplit

    parts = urlsplit(api_base if "//" in api_base else f"//{api_base}")
    host = parts.hostname or ""
    if parts.port is not None:
        return f"{host}:{parts.port}"
    return host or "default"
