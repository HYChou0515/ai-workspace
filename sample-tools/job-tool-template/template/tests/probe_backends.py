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


# Built apart from the raise, so the traceback's source line does not carry it:
# a test that finds it has found the exception message, not the code.
_REASON = "set " + "ACME" + "_TOKEN before importing the backend " + "x" * 5000

if os.environ.get("PROBE_BREAK_IMPORT"):
    raise RuntimeError(_REASON)

if os.environ.get("PROBE_SILENT_EXIT"):
    os._exit(0)  # leaves without a word — and with success

if os.environ.get("PROBE_BANNER_NO_NEWLINE"):
    print("loading acme-sdk…", end="")  # leaves the cursor mid-line

if os.environ.get("PROBE_SLOW_IMPORT"):
    time.sleep(30)
