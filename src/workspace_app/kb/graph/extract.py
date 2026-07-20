"""Metric-claim extraction (#534 slice 1).

Ask a VLM-markdown chunk's text for every metric that carries a numeric value —
`(metric, period, value, unit)` — as JSON, and parse it. The value is kept
VERBATIM ("1.2M", "15%"): normalisation (parsing to a canonical number + unit)
is a later, app-side concern; slice 1 stores what the slide says. Robust: a
non-JSON / malformed reply yields `[]` (never raises), and entries without a
metric name are dropped. Every call streams (`ILlm.collect`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..llm import ILlm

_LOGGER = logging.getLogger(__name__)

_PROMPT = (
    "Extract every metric that carries a numeric value from the passage below. "
    "For each, give the metric name, the time period (empty string if none), the "
    'value VERBATIM (keep the original formatting, e.g. "1.2M", "15%"), and the '
    "unit (empty string if none). Output ONLY a JSON array of objects with keys "
    '"metric", "period", "value", "unit" — no prose.\n\nPassage:\n{text}'
)


@dataclass(frozen=True)
class MetricClaim:
    """One extracted measurement from a slide. ``value`` is the verbatim surface
    form; ``period`` / ``unit`` are ``""`` when absent."""

    metric: str
    period: str = ""
    value: str = ""
    unit: str = ""


def extract_claims(llm: ILlm, text: str) -> list[MetricClaim]:
    """Extract the passage's metric-with-value claims. Never raises."""
    reply = llm.collect(_PROMPT.format(text=text))
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []  # no JSON array in the reply
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        _LOGGER.warning("extract_claims: malformed JSON reply, dropping the batch")
        return []
    if not isinstance(data, list):
        return []
    out: list[MetricClaim] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric", "")).strip()
        if not metric:
            continue  # a claim without a metric name is useless
        out.append(
            MetricClaim(
                metric=metric,
                period=str(item.get("period", "")).strip(),
                value=str(item.get("value", "")).strip(),
                unit=str(item.get("unit", "")).strip(),
            )
        )
    return out
