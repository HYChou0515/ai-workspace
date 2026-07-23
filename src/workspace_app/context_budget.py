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


# ── learning the ceiling from the traffic (#624 P3) ──────────────────
#
# A provider that truncates instead of rejecting tells us nothing on the way in.
# On the way out it does: the reported `prompt_tokens` is what it ACTUALLY read.
# Comparing that against what we believe we sent turns an invisible failure into
# a measurement — and the measured value is its effective window.

#: How far below our estimate the reported count must fall before we call it a
#: cut. The estimate itself runs ~15% off, so the gap has to clear that by a wide
#: margin; a false positive here would trim a user's memory on every later turn.
_TRUNCATION_RATIO = 0.6

#: Prompts below this are too small to judge — a short turn legitimately reports
#: a small count, and a ceiling "learned" from one would be nonsense.
_TRUNCATION_FLOOR_TOKENS = 1_000


def detect_truncation(*, sent_estimate: int, reported_prompt_tokens: int | None) -> int | None:
    """The endpoint's effective window if it silently truncated this request,
    else ``None``.

    Evidence, not suspicion: the provider says it read ``reported_prompt_tokens``
    while we believe we sent ``sent_estimate``. A reported count far below what
    we sent means the front was dropped — the very failure that has no error, no
    warning, and no other symptom except a model that "forgets" and then
    confidently makes something up. Absent/zero usage is silence, not evidence
    (Ollama often streams usage as 0), and a reported count *above* our estimate
    just means we under-counted.
    """
    if not reported_prompt_tokens or reported_prompt_tokens <= 0:
        return None
    if sent_estimate < _TRUNCATION_FLOOR_TOKENS:
        return None
    if reported_prompt_tokens >= sent_estimate * _TRUNCATION_RATIO:
        return None
    return reported_prompt_tokens


class LimitLearner:
    """Per-endpoint memory of the ceiling, learned from observation or rejection.

    Two ways in, with deliberately different burdens of proof:

    - ``learn_exact`` — a rejection stated the limit. That is a fact; take it.
    - ``observe`` — we *inferred* a cut from reported usage. Requires
      ``confirmations`` sightings before it governs anything, because acting on a
      single odd reading would trim every subsequent turn of that conversation.

    In-memory and per-pod on purpose: it is a cache, not a source of truth. A pod
    re-learns within a turn or two, a model swapped behind an endpoint corrects
    itself, and nothing durable can go stale and quietly mis-govern a deploy.
    """

    def __init__(self, *, confirmations: int = 2) -> None:
        self._confirmations = max(1, confirmations)
        self._learned: dict[tuple[str, str], int] = {}
        self._pending: dict[tuple[str, str], list[int]] = {}

    @staticmethod
    def _key(model: str, base_url: str | None) -> tuple[str, str]:
        return (model or "", base_url or "")

    def get(self, model: str, base_url: str | None) -> int | None:
        """The learned ceiling for this endpoint, or None if not established."""
        return self._learned.get(self._key(model, base_url))

    def learn_exact(self, model: str, base_url: str | None, *, limit: int) -> None:
        """Record a ceiling the endpoint stated outright (a rejection). Replaces
        any previous value — endpoints get re-pointed at different models."""
        if limit > 0:
            self._learned[self._key(model, base_url)] = limit
            self._pending.pop(self._key(model, base_url), None)

    def observe(self, model: str, base_url: str | None, *, limit: int) -> None:
        """Record an INFERRED ceiling (from a detected truncation). Governs only
        once seen ``confirmations`` times; the smallest sighting wins, since the
        real window cannot be larger than the least we ever got through."""
        if limit <= 0:
            return
        key = self._key(model, base_url)
        seen = self._pending.setdefault(key, [])
        seen.append(limit)
        if len(seen) >= self._confirmations:
            self._learned[key] = min(seen)
            self._pending.pop(key, None)
