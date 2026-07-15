"""The non-idempotent capability shell (#435) — the framework-locked idempotency 外殼.

A *non-idempotent* capability (mint a numbered entity, send a notification, run an
external op) must not double-fire when a run replays — a crash resume, a gate ``revise``
re-drive, or a re-trigger all re-enter the run from the top. The existing journal already
gives *same-args* idempotency (``run_step`` skips on an unchanged input-hash); this shell
adds the rest **without a bespoke two-phase persistence primitive**: a capability runs as
TWO ordinary journaled steps (decision 1/6) —

    step_<cap>/<key>.decide.json  →  {hash: H(inputs),           result: <Verdict>}
    step_<cap>/<key>.json         →  {hash: H(inputs + verdict), result: <Result>}

``decide`` produces a :class:`Verdict` (the internal dedup ruling — is this new? a
duplicate of an existing one? an exactly-once token?); ``act`` consumes the verdict and
produces the author-visible :class:`Result`. Because ``act``'s input-hash folds in the
verdict, §9 hash-chaining gives the three-state re-run for free:

* both records present + hashes match → BOTH skip → return the cached result;
* verdict present but ``act`` absent/mismatched → decide REUSED, only ``act`` re-runs
  (the act-crash-retry path — decide, the expensive step, is never re-paid);
* neither present → both run.

``act``'s result lands at the capability's *published* path (``key`` = the current scope
key) so ``{steps.<cap>.<field>}`` resolves to ``Result.fields``; the verdict side-car is
private (decision 1: never in the author's reference namespace). The shell is framework-
owned and a capability owner never reimplements it — an owner supplies only the ``decide``
and ``act`` bodies (and the capability's ``outputs`` schema).

The one window the shell does NOT cover — ``act``'s external side effect ran but its
journal write was lost — is inherent to any side-effecting step under ``run_step`` and is
handled *inside* ``act`` per mechanism (an external idempotency key / a send-intent ledger
/ a single FileStore transaction), never by the framework journal.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import msgspec
from msgspec import Struct, field

from .engine import run_step

if TYPE_CHECKING:
    from .handle import WorkflowHandle


logger = logging.getLogger(__name__)


class Verdict(Struct):
    """``decide``'s output — the internal dedup ruling (#435 §4). ``kind`` is the minimal
    shared vocabulary the shell dispatches on: ``new`` (no duplicate — ``act`` creates/
    sends), ``duplicate`` (``act`` merges into / skips the existing one named in
    ``payload``), ``token`` (M2 exactly-once — ``payload`` carries the invocation
    idempotency token). ``payload`` is mechanism-private (``of=<id>`` / ``key=<fingerprint>``
    / ``token=<...>``); the framework treats it as an opaque black box and it never leaks
    into the author-visible ``Result.fields``."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


class Result(Struct):
    """``act``'s output — becomes the capability step's journal ``result`` (#435 §4).
    ``fields`` is the author-visible output, the capability's declared ``outputs``,
    referenceable downstream as ``{steps.<cap>.<field>}``; ``artifact`` is an optional
    produced-file path."""

    fields: dict[str, Any] = field(default_factory=dict)
    artifact: str | None = None


# The two owner-supplied holes. ``decide`` receives the retry feedback (None first try,
# like any ``run_step`` body) and returns a Verdict; ``act`` receives that verdict and
# returns the Result. Neither ever touches the journal.
Decide = Callable[[str | None], Awaitable["Verdict"]]
Act = Callable[["Verdict"], Awaitable["Result"]]


async def run_nonidempotent(
    wf: WorkflowHandle,
    *,
    name: str,
    inputs: Any,
    decide: Decide,
    act: Act,
    key: str = "",
    phase: str = "",
    cache: bool = True,
) -> Result:
    """Run a non-idempotent capability as two journaled steps (decide → act) under one
    ``step_<name>/`` folder. ``key`` is the current scope key ("" at top level, a map
    element's key inside a loop); ``act`` publishes to ``step_<name>/<key>.json`` (the
    author-visible result) and ``decide`` to a private ``…<key>.decide.json`` side-car.
    Framework-locked: the caller (a capability owner) supplies ``decide``/``act`` and
    never journals, skips, or replays by hand."""
    decide_key = f"{key or 'main'}.decide"

    async def _run_decide(feedback: str | None) -> Any:
        return msgspec.to_builtins(await decide(feedback))

    verdict_raw = await run_step(
        wf,
        name=name,
        key=decide_key,
        phase=phase,
        args=inputs,
        execute=_run_decide,
        cache=cache,
    )
    verdict = msgspec.convert(verdict_raw, Verdict)
    logger.info("nonidempotent %s: decide ruled %r (key=%r)", name, verdict.kind, key)

    async def _run_act(_feedback: str | None) -> Any:
        return msgspec.to_builtins(await act(verdict))

    result_raw = await run_step(
        wf,
        name=name,
        key=key,
        phase=phase,
        # act's hash folds in the verdict → a changed ruling re-runs act (§9 chaining).
        args={"inputs": inputs, "verdict": verdict_raw},
        execute=_run_act,
        cache=cache,
    )
    return msgspec.convert(result_raw, Result)
