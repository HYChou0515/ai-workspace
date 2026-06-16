"""ISanityCheck + CheckResult — the #51 LLM sanity-check contract.

Why this exists (the qwen3:14b incident): fake-LLM tests verify OUR
code; only a live canned probe verifies THE MODEL can do the task. A
check is a small, self-contained capability probe ("feed a 3-line
conversation, assert ≥1 valid insight comes back") with a functional
assertion — connectivity (HTTP 200) proves nothing.

Statuses:
  - ``pass``  — probe ran, capability confirmed.
  - ``fail``  — probe ran, the MODEL couldn't do the task (points at
    the model / prompt, not the wiring).
  - ``skip``  — the feature isn't configured (``vlm_llm: null`` →
    the VLM check is skip, not fail).
  - ``error`` — the probe itself couldn't run (connectivity,
    exception): points at the wiring. Checks never raise to callers —
    the runner converts exceptions to this status.
"""

from __future__ import annotations

import abc

from msgspec import Struct

VALID_STATUSES = ("pass", "fail", "skip", "error")


class CheckResult(Struct):
    check_id: str
    status: str  # one of VALID_STATUSES — validated in __post_init__
    detail: str = ""
    latency_ms: int = 0
    checked_at: int = 0  # epoch ms; stamped by the runner

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"unknown status {self.status!r}; valid: {VALID_STATUSES}")


class ISanityCheck(abc.ABC):
    """One capability probe. Bundled probes live under ``health/checks``
    (P2); operators add in-house ones via ``health.checks`` dotted
    paths. Implementations stay simple: do the probe, return a
    CheckResult with ``pass``/``fail``/``skip`` — let exceptions fly,
    the runner turns them into ``error``."""

    # The FE/API key for this check — stable, kebab-case.
    check_id: str
    # Shown on the diagnostics page (operator-facing).
    description: str
    # Fast checks (connectivity-grade, ~seconds) run synchronously at
    # startup; capability probes stay False and run async after boot.
    fast: bool = False

    @abc.abstractmethod
    def run(self) -> CheckResult: ...
