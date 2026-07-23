"""How much context we may use, and how much we are using (#624).

Three pure pieces, deliberately free of I/O so the arithmetic is testable and
the wiring (P2+) can decide *when* to apply it:

- ``resolve_context_limit`` — the ladder that answers "how many tokens may this
  endpoint take?", and says ``unknown`` when nothing can answer. Inventing a
  default is precisely the defect this issue exists for: the two constants that
  govern chat memory today (40 messages / 24,000 tokens) were written for an
  assumed "~32K ctx" that no deployment was ever checked against.
- ``estimate_tokens`` / ``estimate_messages`` — a CJK-aware estimate. The chat
  path used ``chars // 4``, an English rule of thumb that undercounts Traditional
  Chinese ~3.6x (measured: 9,742 chars → 2,435 estimated vs 8,755 real).
- ``history_budget`` — what is left for replayed history once the system prompt,
  the tool schemas and the reply have been paid for. Today's budget ignores all
  three, which is how an 18.5k-token prompt plus a 24k history budget could be
  aimed at a 40,960-token model.

``unknown`` deliberately yields *no* budget rather than a conservative one: with
no known ceiling we send everything and learn the real limit from the response
(P3) or the rejection (P4), instead of silently amputating the user's memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from .kb.tokens import count_tokens

logger = logging.getLogger(__name__)

LimitSource = Literal["config", "learned", "catalog", "unknown"]

#: Headroom kept back from the resolved ceiling to absorb estimator error. The
#: CJK estimate runs ~15% off against a real tokenizer, so aiming exactly at the
#: limit would overshoot on a bad estimate — and an overshoot costs a whole
#: rejected round-trip.
DEFAULT_MARGIN_RATIO = 0.1

#: Tokens held back for the model's own answer. A budget that fills the window
#: with input leaves nothing to reply with.
DEFAULT_REPLY_RESERVE = 2_000


@dataclass(frozen=True)
class ContextLimit:
    """A resolved context ceiling and *where it came from*.

    The source is not decoration: a `learned` value carries different authority
    from a `catalog` guess (it is what the endpoint actually did), and `unknown`
    must stay distinguishable from "some number we made up"."""

    tokens: int | None
    source: LimitSource

    @property
    def known(self) -> bool:
        return self.tokens is not None


def _positive(value: int | None) -> int | None:
    """A limit must be a positive count; 0 / negative / None all mean absent."""
    return value if value is not None and value > 0 else None


def resolve_context_limit(
    *,
    configured: int | None = None,
    learned: int | None = None,
    catalog: int | None = None,
) -> ContextLimit:
    """The ceiling for this endpoint, by descending authority.

    1. ``configured`` — the operator said so. The escape hatch, and it outranks
       everything *for deciding what to send*. (When a rejection later proves a
       configured value wrong, P4 corrects it loudly rather than obeying a
       number reality has disproved — an escape hatch that cannot be overruled
       by evidence is a trap.)
    2. ``learned`` — what the endpoint actually accepted or reported. Beats a
       table, because it is an observation rather than a claim.
    3. ``catalog`` — a registry lookup (litellm). Right for hosted models and
       ``ollama/*``; blank for a self-hosted model served under a custom name.
    4. otherwise ``unknown`` — stated, never faked.
    """
    for value, source in (
        (configured, "config"),
        (learned, "learned"),
        (catalog, "catalog"),
    ):
        got = _positive(value)
        if got is not None:
            return ContextLimit(tokens=got, source=source)
    return ContextLimit(tokens=None, source="unknown")


def catalog_limit(model: str) -> int | None:
    """The registry's input-token ceiling for ``model``, or None when unknown.

    Covers hosted models and ``ollama/*``; a self-hosted model behind an
    OpenAI-compatible endpoint (the production shape) is *not* in any registry,
    so None is the honest and expected answer there — never a fallback number.
    Import is local and every failure degrades to None: a registry lookup must
    not be able to break a turn."""
    if not model:
        return None
    try:
        import litellm

        info = litellm.get_model_info(model)
    except Exception:  # noqa: BLE001 — unknown model / registry shape drift
        return None
    if not isinstance(info, dict):
        return None
    for key in ("max_input_tokens", "max_tokens"):
        got = _positive(info.get(key))
        if got is not None:
            return got
    return None


def estimate_tokens(text: str) -> int:
    """CJK-aware token estimate for ``text`` (see ``kb.tokens.count_tokens``)."""
    return count_tokens(text or "")


def estimate_messages(messages: Any) -> int:
    """Estimated tokens for a list of persisted messages, tool arguments
    included — a large ``patch`` / ``args`` payload occupies the window exactly
    like prose does, and the old estimator counted it too."""
    total = 0
    for m in messages:
        total += estimate_tokens(getattr(m, "content", "") or "")
        args = getattr(m, "tool_args", None)
        if args:
            total += estimate_tokens(str(args))
    return total


def history_budget(
    limit: ContextLimit,
    *,
    overhead_tokens: int,
    reply_reserve: int = DEFAULT_REPLY_RESERVE,
    margin_ratio: float = DEFAULT_MARGIN_RATIO,
) -> int | None:
    """Tokens available for replayed history, or ``None`` when the ceiling is
    unknown — and ``None`` means *do not trim*, not "trim to some default".

    ``overhead_tokens`` is everything sent that is not history: the system
    prompt and the tool schemas. Both were entirely absent from the old
    arithmetic, which is why a deploy could aim 18.5k + 24k at a 40,960 model
    and only find out via silent truncation.
    """
    if limit.tokens is None:
        return None
    usable = int(limit.tokens * (1.0 - margin_ratio))
    return max(0, usable - max(0, overhead_tokens) - max(0, reply_reserve))
