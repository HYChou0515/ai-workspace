"""LLM sanity checks + replay diagnostics (#51).

Public surface:
  - ``ISanityCheck`` / ``CheckResult`` — the capability-probe contract.
  - ``CheckRegistry`` / ``run_check`` — registration + uniform execution.
  - ``ReplayService`` — context-snapshot replays (turn / doc), pure
    LLM probes with zero side effects.

See docs/plan-sanity-checks.md for the locked design.
"""

from .protocol import VALID_STATUSES, CheckResult, ISanityCheck
from .registry import CheckRegistry, run_check
from .replay import (
    ReplayInvalidTarget,
    ReplayRequest,
    ReplayResult,
    ReplayService,
    ReplayToolCall,
    ReplayUnsupported,
)

__all__ = [
    "VALID_STATUSES",
    "CheckRegistry",
    "CheckResult",
    "ISanityCheck",
    "ReplayInvalidTarget",
    "ReplayRequest",
    "ReplayResult",
    "ReplayService",
    "ReplayToolCall",
    "ReplayUnsupported",
    "run_check",
]
