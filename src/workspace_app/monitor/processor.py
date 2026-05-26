"""MonitorProcessor — the bridge from the OpenAI Agents SDK trace stream into a
`IMonitor`. The SDK already emits a `Trace` per run and nested `Span`s for every
LLM generation (with token `usage`), tool call, agent step and handoff; we
register this as a trace processor and mirror that stream verbatim (via the
SDK's own `export()`), so we surface exactly what it reports."""

from __future__ import annotations

from typing import Any

from agents.tracing import Span, Trace, TracingProcessor

from .base import IMonitor, MonitorEvent


class MonitorProcessor(TracingProcessor):
    def __init__(self, monitor: IMonitor) -> None:
        self._m = monitor
        # Spans carry only a trace_id, so remember each trace's group_id (the
        # investigation id the runner tags it with) to stamp onto span events.
        self._groups: dict[str, str | None] = {}

    def on_trace_start(self, trace: Trace) -> None:
        data = trace.export() or {}
        self._groups[trace.trace_id] = data.get("group_id")
        self._m.record({"kind": "trace_start", "group_id": data.get("group_id"), **data})

    def on_trace_end(self, trace: Trace) -> None:
        data = trace.export() or {}
        self._m.record({"kind": "trace_end", "group_id": data.get("group_id"), **data})
        self._groups.pop(trace.trace_id, None)

    def on_span_start(self, span: Span[Any]) -> None:
        self._m.record(self._event("span_start", span))

    def on_span_end(self, span: Span[Any]) -> None:
        self._m.record(self._event("span_end", span))

    def _event(self, kind: str, span: Span[Any]) -> MonitorEvent:
        data = span.export() or {}
        return {"kind": kind, "group_id": self._groups.get(span.trace_id), **data}

    def force_flush(self) -> None:  # nothing buffered — we record synchronously
        pass

    def shutdown(self) -> None:
        pass
