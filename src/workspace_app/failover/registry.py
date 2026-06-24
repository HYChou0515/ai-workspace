"""The process-global cooldown registry shared by every failover role.

Because KB and the app hit the same physical models, "one role found X
overloaded" should make every other role skip X too — so they all share ONE
:class:`CooldownRegistry`, keyed by ``(model, endpoint)``. Cooldown memory is
deliberately per-process (each pod learns independently); there is no
cross-process state to coordinate.

The adapters take a registry argument (tests inject their own for isolation);
the factories pass this shared one.
"""

from __future__ import annotations

import time

from .cooldown import CooldownRegistry

_SHARED: CooldownRegistry | None = None


def get_cooldown_registry() -> CooldownRegistry:
    global _SHARED
    if _SHARED is None:
        _SHARED = CooldownRegistry(clock=time.monotonic)
    return _SHARED
