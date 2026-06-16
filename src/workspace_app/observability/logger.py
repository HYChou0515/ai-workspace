"""`LlmCallLogger` — the litellm `CustomLogger` that records every outbound
call (observability feature B).

Registered once into ``litellm.callbacks``, it fires for EVERY litellm call in
the app (runner chat turns via the agents SDK, KB chat, retrieval enhancements,
VLM, wiki, embeddings) — so coverage needs no per-call-site wiring.

Two invariants the user asked for:
- **不卡 (never blocks):** the file I/O runs off the event loop via
  ``asyncio.to_thread``; the hook only builds the record + hands it off.
- **不錯 (never breaks a turn):** everything is wrapped best-effort — any
  failure is swallowed to a debug log. A broken logger must never surface as a
  failed chat turn.

Generative calls get a full faithful record; embedding/rerank get a one-line
summary (set by ``record.classify_call``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from .record import build_call_record, build_failure_record, build_summary, classify_call
from .writer import LlmLogWriter

_LOGGER = logging.getLogger(__name__)


class LlmCallLogger(CustomLogger):
    def __init__(self, writer: LlmLogWriter) -> None:
        self._writer = writer

    async def async_log_success_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            if classify_call(kwargs.get("call_type")) == "generative":
                record = build_call_record(kwargs, response_obj, start_time, end_time)
                await asyncio.to_thread(self._writer.write_call, record)
            else:
                summary = build_summary(kwargs, response_obj, start_time, end_time)
                await asyncio.to_thread(self._writer.write_summary, summary)
        except Exception:  # noqa: BLE001 — observability must never break a turn
            _LOGGER.debug("llm call log (success) failed", exc_info=True)

    async def async_log_failure_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            exception = kwargs.get("exception", response_obj)
            record = build_failure_record(kwargs, exception, start_time, end_time)
            await asyncio.to_thread(self._writer.write_call, record)
        except Exception:  # noqa: BLE001 — observability must never break a turn
            _LOGGER.debug("llm call log (failure) failed", exc_info=True)
