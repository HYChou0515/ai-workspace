"""Backends whose modules misbehave when imported — loaded only by the
cross-process probe, never by the tests themselves."""

from __future__ import annotations

import os
import time

from backends import SteadyOverride

# A library banner with a timestamp: different in every process.
print(f"acme-sdk loaded at {time.time()}")


class Noisy(SteadyOverride):
    """Steady sameness, noisy import."""


if os.environ.get("PROBE_BREAK_IMPORT"):
    raise RuntimeError("set ACME_TOKEN before importing the backend")
