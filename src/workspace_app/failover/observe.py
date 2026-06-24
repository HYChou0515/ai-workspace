"""Observability for busy-aware failover (#196).

Every outbound attempt is already in the faithful LLM call log (the global litellm
``CustomLogger``), so "tried A → then B" is visible there for free. This adds the
one thing that log can't infer: an explicit, greppable WARNING at the moment the
chain *switches*, naming the role, the model that was dropped, and why — so an
operator can see a deployment running degraded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

_LOGGER = logging.getLogger("workspace_app.failover")


def make_switch_logger(role: str) -> Callable[[str, BaseException], None]:
    """An ``on_switch(model_label, cause)`` callback that logs the degrade for
    ``role`` (e.g. ``"kb-retrieval"``, ``"vlm"``, ``"agent"``)."""

    def on_switch(model_label: str, cause: BaseException) -> None:
        _LOGGER.warning(
            "failover[%s]: model %r is busy/failed (%s) — switching to next in chain",
            role,
            model_label,
            cause,
        )

    return on_switch
