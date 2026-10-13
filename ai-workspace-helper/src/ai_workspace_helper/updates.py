"""The running helper keeps itself current (docs/plan-outside-helper.md
§3.7.4–§3.7.6).

`tick` runs on the Playwright thread with the job loop: it reads the published
release and fetches the zip through the helper's own signed-in line (Playwright
is not thread-safe). Installing it — a venv and pip, about a minute — runs on a
background thread, so lookups go on meanwhile; the result waits for the next
start (`setup_core.prepare_update`). `status` is what the window shows.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import setup_core
from .version import __version__

#: §3.7.4: at start, then this often.
CHECK_EVERY_S = 6 * 3600
#: A refused helper (§3.7.6) retries a failed update this much later, not on
#: every poll.
RETRY_AFTER_S = 600

logger = logging.getLogger(__name__)


def _thread(work: Callable[[], None]) -> None:  # pragma: no cover — tests run it inline
    threading.Thread(target=work, name="ai-workspace-helper-update", daemon=True).start()


class Updater:
    def __init__(
        self,
        home: Path,
        backend: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _thread,
        run: setup_core.Run = setup_core.run_quietly,
        version: str = __version__,
    ) -> None:
        self._home = home
        self._backend = backend
        self._clock = clock
        self._spawn = spawn
        self._run = run
        self._version = version
        self._next = 0.0
        self._retry_at = 0.0
        self._busy = False
        self._confirmed = False
        #: "" | "preparing" | "ready" | "failed: <why>" — for the window.
        self.status = ""

    def connected(self) -> None:
        """This version runs (the Controller calls it on the statuses that show
        that): if it is the version waiting to be tried, it has proven itself
        and becomes current (§3.7.5 step 3)."""
        if self._confirmed:
            return
        self._confirmed = True
        if setup_core.confirm(self._home, self._version):
            logger.info("confirmed %s as the current version", self._version)

    def report(self) -> str:
        """The last update's outcome, for the check-in (§3.7.7)."""
        return str(setup_core.read_state(self._home).get("update") or "")

    def tick(self, required: dict[str, Any] | None = None) -> None:
        """Check for a newer release when one is due — or, with `required`
        (the release a 426 named, §3.7.6), at once."""
        if self._busy:
            return
        now = self._clock()
        if required is None:
            if now < self._next:
                return
            self._next = now + CHECK_EVERY_S
            manifest = self._backend.manifest()
        else:
            if now < self._retry_at:
                return
            manifest = required
        if not manifest:
            return
        settled = setup_core.update_outcome(self._home, setup_core.read_state(self._home), manifest)
        if settled == "ready":
            self.status = "ready"
        elif settled == "bad" and required is not None:
            # refused, and the one release it may update to failed here
            # before: only a reinstall is left (§3.7.6)
            self.status = "failed: 這個新版先前在這台電腦裝不起來"
        if settled is not None:
            return
        try:
            data = self._backend.download(str(manifest["file"]))
        except Exception as exc:  # noqa: BLE001 — shown; the running version carries on
            self._failed(exc)
            return
        self._busy, self.status = True, "preparing"

        def work() -> None:
            try:
                setup_core.prepare_update(self._home, manifest, lambda _f: data, run=self._run)
                self.status = "ready"
            except Exception as exc:  # noqa: BLE001 — shown; the running version carries on
                self._failed(exc)
            finally:
                self._busy = False

        self._spawn(work)

    def _failed(self, exc: BaseException) -> None:
        logger.warning("update failed: %s", exc)
        self.status = f"failed: {exc}"
        self._retry_at = self._clock() + RETRY_AFTER_S


__all__ = ["CHECK_EVERY_S", "RETRY_AFTER_S", "Updater"]
