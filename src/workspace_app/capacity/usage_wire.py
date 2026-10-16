"""The usage record's wire format (docs/plan-capacity-usage.md §5): one JSON
object per line, routed by its collector on `event.dataset`.

ONE implementation, kept byte for byte identical with sandbox-host's copy
(`sandbox-host/src/sandbox_host/usage_wire.py`; `tests/capacity/test_copies.py`
compares the bytes): sandbox-host writes the `sandbox` rows and cannot import
this package. Standard library only, for the same reason.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = 1
#: What the collector routes on (D3): every record carries it, nothing else does.
DATASET = "workspace.usage"


def iso_ms(at_ms: int) -> str:
    """`2026-10-15T04:05:00.000Z`: a date ES maps without being told a format."""
    stamp = dt.datetime.fromtimestamp(at_ms // 1000, dt.UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at_ms % 1000:03d}Z"


def window_start(at_ms: int, window_s: int) -> int:
    """The start of the window `at_ms` falls in. Windows start on multiples of
    their length since the epoch, so a date histogram whose interval is a
    multiple of `window_s` puts each window in exactly one bucket."""
    return at_ms - at_ms % (window_s * 1000)


def document(
    kind: str, at_ms: int, *, pod: str, component: str, fields: Mapping[str, Any]
) -> dict[str, Any]:
    """One record. `at_ms` is the start of the interval the numbers describe,
    or the moment the event began.

    A field whose value is None is left out: it is one the source could not
    read (a kernel without PSI), and writing 0 would claim a measurement."""
    usage: dict[str, Any] = {"v": SCHEMA_VERSION, "kind": kind, "pod": pod, "component": component}
    usage.update((k, v) for k, v in fields.items() if v is not None)
    return {"@timestamp": iso_ms(at_ms), "event": {"dataset": DATASET}, "usage": usage}


def line(doc: Mapping[str, Any]) -> str:
    """The record as one line of JSON. ASCII only, so no pipeline's encoding
    can split a character in a person's name."""
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=True)


def pod_name() -> str:
    """This process's pod as Kubernetes names it (`HOSTNAME`), else the host's name."""
    return os.environ.get("HOSTNAME") or socket.gethostname()
