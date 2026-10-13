"""The helper's loop (docs/plan-outside-helper.md §3.4).

One `tick`: check in, take the person's next job, run its errand in the
browser, hand the page back — or report why it could not. The window calls
`tick` on a timer; everything that decides anything is here, so it is tested
against the real backend without a window or a real browser.

`status` is what the window shows: `connected`, `paused`, `needs_login`,
`update_required`, `unreachable`, or `error`.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable, Mapping
from typing import Any

from .backend import Backend, BackendError, NeedsLogin, UpdateRequired
from .errands import Errand

logger = logging.getLogger(__name__)

#: Longest failure reason sent: the card shows it in one line.
_ERROR_CHARS = 500


def _today() -> str:
    return datetime.date.today().isoformat()


class Worker:
    def __init__(
        self,
        backend: Backend,
        browser: Any,
        *,
        errands: Mapping[str, Errand],
        date: Callable[[], str] = _today,
        info: Callable[[], Mapping[str, str]] = dict,
    ) -> None:
        self._backend = backend
        self._browser = browser
        self._errands = errands
        self._date = date
        #: What each check-in reports beyond the version (§3.7.7).
        self._info = info
        self.paused = False
        self.status = "starting"
        #: What the window shows under the status: the errand under way, or the
        #: backend's reason for a refusal.
        self.detail = ""
        self.manifest: Any = None
        #: Told (status, detail) the moment an errand starts or ends — the
        #: window shows 「正在查:…」 while it runs, not once the poll is over.
        self.on_change: Callable[[str, str], None] | None = None

    def tick(self) -> None:
        if self.paused:
            # Not checking in: the card falls back to the manual flow (D7)
            # rather than offering a 「查」 nobody will act on.
            self.status, self.detail = "paused", ""
            return
        try:
            self._backend.heartbeat(**self._info())
            self.status, self.detail = "connected", ""
            job = self._backend.claim()
            if job is not None:
                self._run(job)
        except NeedsLogin:
            self.status, self.detail = "needs_login", ""
        except UpdateRequired as exc:
            self.status, self.manifest = "update_required", exc.manifest
        except OSError as exc:
            self.status, self.detail = "unreachable", str(exc)
        except BackendError as exc:
            self.status, self.detail = "error", str(exc)

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self.status, self.detail)

    def _run(self, job) -> None:  # noqa: ANN001 — backend.Job
        errand = self._errands.get(job.kind)
        if errand is None:
            self._backend.fail(job.id, f"this helper cannot do {job.kind!r} — update the helper")
            return
        self.detail = job.args
        self._changed()
        try:
            page = self._browser.run(
                errand, job.args, lambda text: self._backend.note(job.id, text)
            )
        except (NeedsLogin, UpdateRequired, OSError, BackendError):
            raise
        except Exception as exc:  # noqa: BLE001 — any errand failure is reported to the card
            logger.warning("errand %s failed", job.kind, exc_info=True)
            self._backend.fail(job.id, (str(exc) or type(exc).__name__)[:_ERROR_CHARS])
            return
        finally:
            self.detail = ""
            self._changed()
        self._backend.result(
            job.id, title=page.title, url=page.url, markdown=page.markdown, date=self._date()
        )


__all__ = ["Worker"]
