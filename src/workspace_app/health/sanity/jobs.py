"""Durable queue types for the model-sanity battery.

A run is a specstar Job (like the index / wiki coordinators) so the heavy LLM
work happens off the request, on a job pod. ``partition_key`` = the model, so a
single model's cells run serially across consumers (Ollama serves one model at a
time anyway — no point hammering it from two pods).

The FE triggers a run by creating one of these (specstar's auto ``POST
/sanity-run`` route); the handler completes the cell(s) and writes
``SanityResult`` rows the FE then lists.
"""

from __future__ import annotations

import msgspec
from specstar.types import Job


class SanityRunPayload(msgspec.Struct):
    """One sanity run. ``scope`` selects the work:

    - ``cell`` — run exactly one matrix cell (``question_key`` × ``level``).
    - ``battery`` — run every auto-run cell for ``model`` (each auto_run
      question at its own auto_levels).
    """

    model: str
    scope: str = "cell"  # cell | battery
    question_key: str = ""  # required for scope=cell
    level: str = ""  # required for scope=cell


class SanityRun(Job[SanityRunPayload]):
    """A queued sanity run. ``partition_key`` is the model so one model's runs
    are serial across consumers."""
