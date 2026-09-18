"""The pod's shutdown, begun at the signal (plan-graceful-shutdown P2).

What SIGTERM did before this: uvicorn stopped listening, asked every connection
to finish its response, and waited — with the default
``timeout_graceful_shutdown=None``, forever — before sending the lifespan its
shutdown event. A chat's SSE stream heartbeats until the tab closes, so with
one chat open the lifespan never ran: not the turn drain (#558), not the
sandbox teardown. kubelet SIGKILLed the pod at the grace period, every time.
(Probed on the real app: ``Shutting down`` → ``Waiting for connections to
close.`` → still running 20 s later.)

So the shutdown starts where the signal lands. :class:`DrainingServer`
overrides uvicorn's ``handle_exit`` — the documented seam, run in the signal
handler on the loop thread but outside any task — and hands :meth:`Drain.begin`
to the loop before letting uvicorn set ``should_exit`` as it always did.
``begin`` flips readiness off (``/api/readyz`` answers 503, so k8s stops
routing here; the Deployment's ``preStop`` sleep gives the endpoints time to
notice) and ends every live stream: the chat engines close their subscribers
(the FE treats a closed stream as "reconnect now" and lands on a live pod), the
monitor feed closes its. With the connections gone uvicorn's wait ends within
a second; ``timeout_graceful_shutdown`` (``server.shutdown_budget_sec``) is the
safety net for anything that did not, so no connection can hold the lifespan
shutdown again. The lifespan then drains in-flight turns for the same budget
and tears down as written.

Two budgets, ONE number: uvicorn's connection wait and the lifespan's drain
(one deadline for every engine and, all-in-one, the coordinators) both get
``shutdown_budget``; each engine with turns past its deadline adds 4 s (the
handover write, the cancelled turns' teardown). So the worst case is
``2 × budget + 8 s`` plus teardown — the k8s ``terminationGracePeriodSeconds``
must exceed that, and its ``preStop`` sleep counts inside it
(``kubernetes/base/deployment.yaml`` says by how much).

On a pod deletion the readiness 503 is NOT what stops traffic: k8s removes a
Terminating pod from the EndpointSlice on its own, in parallel with preStop →
SIGTERM, and preStop's sleep is what lets that removal propagate before the
listener closes. The 503 covers the SIGTERMs that are not deletions — a
liveness restart of the container, a manual kill — where the pod stays in
the endpoints.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from types import FrameType

import uvicorn

logger = logging.getLogger(__name__)


class Drain:
    """The one place a pod's shutdown begins. Stream sources register a closer
    with :meth:`on_begin`; :meth:`begin` runs them all, once, on the loop."""

    def __init__(self) -> None:
        self._draining = False
        self._closers: list[Callable[[], None]] = []

    @property
    def draining(self) -> bool:
        return self._draining

    def on_begin(self, closer: Callable[[], None]) -> None:
        """Register something to end when the drain begins — a chat engine's
        subscribers, the monitor feed. Called during composition."""
        self._closers.append(closer)

    def begin(self) -> None:
        """Idempotent: a second signal changes nothing. Per-closer resilient: a
        source that raises must not leave the others open — a drain that
        half-runs is the SIGKILL this replaces."""
        if self._draining:
            return
        self._draining = True
        logger.info("drain: begun — readiness off, ending %d stream source(s)", len(self._closers))
        for closer in self._closers:
            try:
                closer()
            except Exception:
                logger.exception("drain: a stream source failed to close; continuing")


class DrainingServer(uvicorn.Server):
    """uvicorn's ``Server`` with the drain wired into its signal handler.

    ``handle_exit`` runs inside the signal handler: on the loop thread, between
    two bytecodes of whatever the loop was doing, outside any task. Nothing
    async can happen there, so the drain is *scheduled* with
    ``call_soon_threadsafe`` — which also wakes a loop parked in ``select()``
    — and uvicorn's own bookkeeping (``should_exit``) proceeds unchanged. A
    loop that is not running yet (a signal during boot) has nothing to drain."""

    def __init__(self, config: uvicorn.Config, *, drain: Drain) -> None:
        super().__init__(config)
        self._drain = drain

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        with contextlib.suppress(RuntimeError):  # no running loop: still booting
            asyncio.get_running_loop().call_soon_threadsafe(self._drain.begin)
        super().handle_exit(sig, frame)
