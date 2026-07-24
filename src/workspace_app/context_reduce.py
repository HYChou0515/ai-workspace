"""What to give up when the context does not fit — the interface (#624).

The first cut of this issue hardcoded one answer, "drop the oldest messages",
into the request path itself. That is not a detail: it is a product policy, and
on the measured evidence it is close to the worst available one. A work session
whose tool output dwarfs its dialogue (one `exec` can legitimately return 30 KB,
roughly twenty turns' worth of budget) loses twenty turns of conversation in
order to keep one data dump — and the FIRST thing it sacrifices is the user's
opening message, which is usually the task everything else refers back to.

So the decision belongs behind a seam. ``IContextReducer`` says only: given a
thread and a token budget, produce a thread that fits, and be able to explain
what you gave up. Which policy a deployment runs is a configured choice; the
request path does not get to assume one.

Impls live in ``context_reducers`` (interface / implementation split, per the
repo's ABC convention).
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: Measures a list of messages in tokens. Injected so a reducer never has to
#: know how counting works (and so tests can make the arithmetic obvious).
Estimator = Callable[[Sequence[Any]], int]


@dataclass(frozen=True)
class ReductionResult:
    """A thread that fits, plus what it cost to make it fit.

    ``summary`` exists because "N messages were dropped" is only true for one
    of the strategies — an eliding reducer keeps every message and shrinks
    some, a summarising one would replace a span with a précis. The user-facing
    notice renders this, so it must describe what actually happened rather than
    assume the incumbent policy."""

    messages: list[Any]
    summary: str = ""
    changed: bool = False


class IContextReducer(abc.ABC):
    """Fit a thread into a token budget, and say what was given up."""

    #: Stable identifier used to select this policy in configuration.
    name: str = ""

    @abc.abstractmethod
    def reduce(
        self, messages: Sequence[Any], *, budget: int, estimate: Estimator
    ) -> ReductionResult:
        """Return a thread that fits ``budget``.

        Two invariants every policy must hold, because breaking either is worse
        than any amount of forgetting:

        - the newest message survives — dropping the turn's own context makes
          the reply incoherent rather than merely forgetful;
        - a thread that already fits is returned untouched, with
          ``changed=False``, so no notice is raised for a non-event.
        """
