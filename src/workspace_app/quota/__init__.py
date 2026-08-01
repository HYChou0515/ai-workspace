"""Resource limits — how much ONE item may consume, and how much ONE person may
hold across every item they own.

Two mechanisms live here because the two resources behave differently:

- **disk is a stock.** It persists after every sandbox is gone, so it is summed
  over an owner's workspaces and checked at write time. It only ever blocks
  GROWTH — shrinking, same-size replaces and deletes always pass, or a person at
  their cap could never get back under it.
- **cpu / memory are flows.** They exist only while a sandbox is alive and are
  reclaimed when it is reaped, so they need no durable ledger — only admission
  control at the moment a NEW sandbox would be created, over a tally derived
  from what is provably alive.

The debtor for both is the item's ``owner`` field (see #687, which locks that
field down and adds a consent-based transfer — until it lands, the debtor is
rewritable and these limits are bypassable by design-in-progress).
"""

from __future__ import annotations

from .limits import (
    ResourceLimitError,
    ResourceLimits,
    format_cgroup_size,
    parse_size,
    resolve_app_limits,
    validate_app_resources,
    validate_discovered_apps,
)

__all__ = [
    "ResourceLimitError",
    "ResourceLimits",
    "format_cgroup_size",
    "parse_size",
    "resolve_app_limits",
    "validate_app_resources",
    "validate_discovered_apps",
]
