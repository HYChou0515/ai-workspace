"""Make a `workspace_app.*` logger's INFO lines actually appear.

This app configures Python logging nowhere — ``uvicorn.run`` is called with no
``log_config`` and no ``log_level``, and uvicorn's defaults only touch its own
loggers. So ``workspace_app.*`` records fall through to the stdlib's handler of
last resort, which drops everything below WARNING: an INFO-level line emits
absolutely nothing, and the only way to discover that is to deploy and see an
empty log (#938's `event_bus: connected` check would have found nothing on a
healthy pod).
"""

from __future__ import annotations

import logging
import sys

_MARK = "_workspace_app_stdout"


def install_own_handler(
    logger: logging.Logger,
    *,
    fmt: str = "%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt: str | None = None,
    propagate: bool = True,
) -> None:
    """Give `logger` its own stdout handler and INFO level — once, however
    often it is asked. `propagate` stays on unless asked otherwise, so a test's
    log capture (and any logging someone does configure) still sees the lines."""
    if any(getattr(h, _MARK, False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = propagate
