"""How much context we may use, and how much we are using (#624).

The arithmetic here is pure, so it is testable and the wiring (P2+) decides
*when* to apply it. The two pieces that are NOT pure are named as such:
``catalog_limit`` does network I/O despite reading like a table lookup, and
``deferred_lookup`` is the mechanism that keeps it — and the ``/tokenize`` probe
— off the event loop.

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

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .kb.tokens import count_tokens

logger = logging.getLogger(__name__)

LimitSource = Literal["config", "learned", "catalog", "unknown"]

#: Strong refs to in-flight deferred lookups — asyncio keeps only a weak one, so
#: an unreferenced task can be collected mid-flight.
_INFLIGHT: set[asyncio.Task[None]] = set()


def deferred_lookup(cache: dict[Any, Any], key: Any, compute: Callable[[], Any]) -> Any:  # noqa: ANN401 — deliberately value-agnostic
    """A value that may do I/O, read from a place that must not block (#624).

    Two rungs of the ceiling ladder are read from ``_budget_for``, which runs
    inside ``async def build_chat_turn``, and both do network I/O: the
    ``/tokenize`` probe (a synchronous POST, 3s timeout) and ``catalog_limit``,
    which looks like a table lookup and is not — litellm resolves an ``ollama/*``
    name by asking the daemon, with no timeout at all. Measured against an
    address that does not answer: **129,781 ms**, with the pod's whole event loop
    frozen for all of it.

    So: return what is cached, and otherwise answer ``None`` — which needs no
    compensating design, because ``unknown`` already means "send it all" (§3).
    The value is computed in a thread and is there for the next turn. A caller
    with no running loop has nothing to protect, so it simply computes.

    Failure is cached as silence: a broken endpoint retried every turn is how one
    outage becomes a thread per message.
    """
    if key in cache:
        return cache[key]

    def _fill() -> None:
        try:
            cache[key] = compute()
        except Exception:  # noqa: BLE001 — a lookup must never break a turn
            logger.debug("deferred lookup failed for %r", key, exc_info=True)
            cache[key] = None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _fill()
        return cache[key]
    cache[key] = None  # claim it: one lookup per key, however many turns arrive
    task = loop.create_task(asyncio.to_thread(_fill))
    _INFLIGHT.add(task)
    task.add_done_callback(_INFLIGHT.discard)
    return None


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

    **This does network I/O.** It reads like a table lookup and mostly is one —
    hosted models really are in litellm's bundled map — but an ``ollama/*`` name
    is NOT in that map: litellm resolves it by asking the daemon, with no
    timeout. Measured against an address that does not answer: 129,781 ms. So
    every caller on a request path must reach it through ``deferred_lookup``,
    never directly.

    A self-hosted model behind an OpenAI-compatible endpoint (the production
    shape) is in no registry, so None is the honest and expected answer there —
    never a fallback number. Import is local and every failure degrades to None:
    a registry lookup must not be able to break a turn."""
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
    """Estimated tokens for a list of messages, tool arguments included — a large
    ``patch`` / ``args`` payload occupies the window exactly like prose does.

    Handles BOTH shapes the system carries a message in: a persisted ``Message``
    object and an SDK input item (a plain dict). Counting only one of them does
    not fail loudly — it silently returns 0 for the other, so everything "fits",
    every policy becomes a no-op, and the caller falls back to whatever blunt
    rule it kept for emergencies. That is precisely how the retry path came to
    drop the user's opening request while a policy that would have preserved it
    ran and reported success."""
    total = 0
    for m in messages:
        if isinstance(m, dict):
            total += estimate_tokens(str(m.get("content", "") or ""))
            args = m.get("arguments") or m.get("tool_args")
        else:
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


#: Role of a compaction summary (#739). Defined here, not in `api.turns`, so the
#: arithmetic and the replay agree by construction: this module is imported BY
#: the API and must never import back from it, and a role string spelled in two
#: places is a rule that will hold in one of them.
SUMMARY_ROLE = "summary"


# ── what the thread is actually costing (#739 P1) ────────────────────


@dataclass(frozen=True)
class ContextUsage:
    """How much of the window this thread occupies, and how much of that we
    actually KNOW rather than guess."""

    used: int
    limit: int | None
    measured: bool

    @property
    def ratio(self) -> float | None:
        """How full the window is, or ``None`` when no ceiling is known.

        ``None`` means *show no denominator*, not "assume a default". A bar
        drawn against an invented ceiling is a number nobody measured that
        everybody believes — the #624 disease."""
        if not self.limit:
            return None
        return self.used / self.limit


def _replayed(messages: list[Any]) -> list[Any]:
    """The subset that actually reaches the model. A marker the FE renders but
    `history_items` never replays costs the window nothing, and charging for it
    would make the bar creep up on its own."""
    kept: list[Any] = []
    for m in messages:
        role = getattr(m, "role", "")
        if role == "notice":
            continue
        # #37: a terminal failure is a human-only diagnostic and never re-enters
        # the model's context — EXCEPT a user cancellation, which is replayed as
        # a short marker folded onto the preceding assistant turn (#199).
        if role == "error" and getattr(m, "error_kind", None) != "cancelled":
            continue
        kept.append(m)
    return kept


def context_usage(messages: Any, *, limit: ContextLimit) -> ContextUsage:
    """The thread's current context cost.

    Anchored on the newest turn the provider itself measured: its reported
    ``prompt_tokens`` is what the endpoint actually read, including the system
    prompt, the tool schemas and the skills index — none of which the estimator
    can see. Only what arrived after that turn is estimated, so the error is
    bounded by one turn's worth of new messages instead of the whole history.

    The anchor's own answer is added from ``completion_tokens``: ``prompt_tokens``
    is input only, so the reply was not in the window it measured but will be in
    the next one."""
    msgs = list(messages)
    summarised_at: int | None = None
    # #739: a summary is the new beginning of the thread. Everything behind it
    # was replaced by the précis, so it no longer occupies the window — and the
    # last reported `prompt_tokens` counted a request that still contained it,
    # which makes that measurement an answer to a question no longer being
    # asked. Dropping the anchor is the honest outcome: the figure goes back to
    # an estimate, and `measured` says so, until the next turn reports a real one.
    for i in range(len(msgs) - 1, -1, -1):
        if getattr(msgs[i], "role", "") == SUMMARY_ROLE:
            summarised_at = getattr(msgs[i], "created_at", None)
            msgs = msgs[i:]
            break
    for i in range(len(msgs) - 1, -1, -1):
        metrics = getattr(msgs[i], "metrics", None)
        reported = getattr(metrics, "prompt_tokens", 0) or 0
        # A summary is INSERTED before the kept tail, so the messages after it
        # are OLDER than it, and their counts were reported for requests that
        # still contained the span it replaced. Anchoring on one reports the
        # pre-compaction figure forever — the bar sits still and the feature
        # reads as broken. (Found by pressing compact on a running app.)
        stale = (
            summarised_at is not None
            and (getattr(msgs[i], "created_at", None) or 0) < summarised_at
        )
        # #739: and it has to be the provider's number, not ours. A reported 0
        # never reaches the store — the runner substitutes our estimate so the
        # live ↑ does not flip to 0 — so `reported > 0` cannot tell a
        # measurement from a guess, and the refuse-a-zero rule above is
        # unreachable on any deployment whose provider stays quiet. Absent
        # (threads older than the flag) counts as not exact.
        if getattr(msgs[i], "role", "") == "assistant" and reported > 0 and not stale:
            used = (
                reported
                + (getattr(metrics, "completion_tokens", 0) or 0)
                + estimate_messages(_replayed(msgs[i + 1 :]))
            )
            # #739: the figure is the best one available either way — when the
            # provider reports nothing the runner substitutes its own
            # WHOLE-REQUEST estimate, which still counts the system prompt and
            # tool schemas that `estimate_messages` cannot see. Rejecting the
            # record outright made the number worse (measured: from +500 off to
            # −5,800 off on a 32k thread) and stopped the compaction trigger
            # firing on a full window. So keep the number; only the label is in
            # question. `exact` is the provider's word, and absent — a thread
            # older than the field — is not a yes.
            return ContextUsage(
                used=used,
                limit=limit.tokens,
                measured=bool(getattr(metrics, "exact", False)),
            )
    return ContextUsage(used=estimate_messages(_replayed(msgs)), limit=limit.tokens, measured=False)


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


# ── the rejection path (#624 P4) ─────────────────────────────────────
#
# A provider that rejects an over-long request is being helpful: the message
# states its ceiling. Today that information is discarded and the same prompt is
# re-sent up to three times, each attempt carrying an appended "try again" hint
# that makes it *longer*. None of those attempts can succeed.

#: Phrases that mark a 400 as "too long" rather than "malformed". Matched
#: loosely because the exact wording differs across OpenAI-compatible servers,
#: but narrowly enough that an ordinary bad request is not swept in — halving
#: the history cannot fix a bad parameter, and retrying one at all is waste.
_OVERFLOW_MARKERS = (
    "maximum context length",
    "context_length_exceeded",
    "prompt is too long",
    "too many tokens",
    "reduce the length",
    "max_model_len",
)

_LIMIT_PATTERNS = (
    # "This model's maximum context length is 32768 tokens"
    r"maximum context length is (\d+)",
    # "max_model_len (8192)" / "max_model_len=8192"
    r"max_model_len[^\d]{0,4}(\d+)",
    # "9000 tokens > 8192" — the ceiling is the right-hand side
    r"\d+\s*tokens?\s*>\s*(\d+)",
)


def is_context_overflow(message: str) -> bool:
    """Whether this provider error means "the prompt did not fit".

    The distinction is the whole point: a length rejection is worth acting on
    (shrink and retry, and remember the ceiling), while every other 400 is
    deterministic and must fail immediately instead of burning three identical
    attempts."""
    low = (message or "").lower()
    return any(marker in low for marker in _OVERFLOW_MARKERS)


def parse_limit_from_error(message: str) -> int | None:
    """The ceiling the endpoint stated in its rejection, or None.

    None means "it did not say" — never a fallback number. A ceiling invented
    here would go on to govern every later turn of every conversation on this
    endpoint."""
    import re

    for pattern in _LIMIT_PATTERNS:
        m = re.search(pattern, message or "", flags=re.IGNORECASE)
        if m:
            value = _positive(int(m.group(1)))
            if value is not None:
                return value
    return None


def halve_history(messages: list[Any]) -> list[Any]:
    """Drop the older half, keeping the newest — the productive retry.

    Converges in a handful of rounds (40 → 20 → 10 → …) and floors at one
    message: a single message that alone exceeds the window is a fail-loud case
    ("this message is too large", naming it), never something to loop on.
    """
    if len(messages) <= 1:
        return list(messages)
    return list(messages[len(messages) // 2 :])
