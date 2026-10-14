"""The running helper says when a newer version is out
(docs/plan-outside-helper.md D21, §3.7.4, §3.7.6).

It only tells. The helper's line to AI Workspace does not verify the
certificate (D20), so whatever came down it could have been replaced on the
way: the person downloads the zip in their own browser, which verifies, and
runs install.pyw again (§3.7.5). `tick` runs on the Playwright thread with
the job loop (Playwright is not thread-safe); `status` is what the window
shows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .setup_core import version_key
from .version import __version__

#: §3.7.4: on the first connected poll (and the first after a refusal), then
#: this often.
CHECK_EVERY_S = 6 * 3600


class Updater:
    def __init__(
        self,
        backend: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        version: str = __version__,
    ) -> None:
        self._backend = backend
        self._clock = clock
        self._version = version
        self._next = 0.0
        #: "" | "available: <version>" | "required: <version or nothing>".
        self.status = ""

    def tick(self, required: Any = None) -> None:
        """Read the published release when a check is due — or, with
        `required` (what a 426 named, §3.7.6), say at once that this version
        is refused and which release to install."""
        if required is not None:
            # it came down the unverified line (D20): only a release that is a
            # dict names a version
            named = required.get("version") if isinstance(required, dict) else None
            self.status = f"required: {named or ''}"
            # let back in (min_version lowered), it reads the release at once
            # rather than saying 「下載新版」 until the next six-hour check
            self._next = 0.0
            return
        now = self._clock()
        if now < self._next:
            return
        self._next = now + CHECK_EVERY_S
        try:
            manifest = self._backend.manifest() or {}
        except Exception:
            # connected, this version is not refused: a failed read must not
            # leave 「下載新版」 up until the next check
            if self.status.startswith("required"):
                self.status = ""
            raise
        version = str(manifest.get("version") or "")
        newer = version_key(version) > version_key(self._version)
        self.status = f"available: {version}" if newer else ""


__all__ = ["CHECK_EVERY_S", "Updater"]
