"""The pipe a delegating tool hands its sub-agent run (docs/plan-friendly-errors.md P4).

Every delegation path already passes ONE thing all the way down to the relay
that watches the sub-agent: the sink that writes under the tool's card. Each
layer between (the bridge, the per-turn depth wrapper, the workflow and
configured ask_knowledge_base wrappers) forwards it untouched. So the two
other things the sub-agent has to tell its caller ride on that same object
rather than as new parameters every layer would have to remember to forward:

* a hold or a retry (`RateLimited`, `ModelRetrying`) is the delegating TURN
  waiting — it goes to the turn's status line, not under a card that has
  stopped moving;
* a failure goes back to the tool, which records it against its own call so the
  card can say what happened while the model still reads the raw text.

It is still a plain output sink to anything that only writes bytes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..sandbox.protocol import OutputSink


class Delegation:
    def __init__(
        self,
        emit: OutputSink | None,
        *,
        status: Callable[[Any], None] | None = None,
        on_failure: Callable[[Any], None] | None = None,
    ) -> None:
        self._emit = emit
        self._status = status
        self._on_failure = on_failure

    def __call__(self, data: bytes) -> None:
        if self._emit is not None:
            self._emit(data)

    def status(self, event: Any) -> None:
        """A hold or a retry of the sub-agent's — this turn waiting."""
        if self._status is not None:
            self._status(event)

    def failed(self, error: Any) -> None:
        """The sub-agent's run ended on this `RunError`."""
        if self._on_failure is not None:
            self._on_failure(error)
