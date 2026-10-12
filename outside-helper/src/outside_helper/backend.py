"""Talking to the AI workspace backend (docs/plan-outside-helper.md §3.2, §3.7.3).

The transport is injected (`Send`): on the PC it is Playwright's request
context, which shares the cookie jar of the helper's own Chrome profile — the
person signed in there, through the deploy's normal login (D6) — and in tests it
is the app itself. Every request says which helper version and protocol it is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .version import PROTOCOL, __version__

#: (method, path, json body or None, headers) -> (status, decoded json or None).
#: Raises OSError when the backend cannot be reached at all. Must NOT follow
#: redirects: a redirect is the gateway sending an expired login to sign in.
Send = Callable[[str, str, "dict[str, Any] | None", dict[str, str]], tuple[int, Any]]


class NeedsLogin(Exception):
    """The deploy's login is missing or expired: the person signs in again."""


class UpdateRequired(Exception):
    """The backend no longer accepts this version (426); `manifest` says where
    the current one is."""

    def __init__(self, manifest: Any) -> None:
        super().__init__("this helper version is no longer accepted")
        self.manifest = manifest


class BackendError(Exception):
    """Any other refusal, with the backend's reason."""


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    args: str


class Backend:
    def __init__(self, send: Send) -> None:
        self._send = send

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {
            "X-Outside-Helper-Version": __version__,
            "X-Outside-Helper-Protocol": str(PROTOCOL),
        }
        status, data = self._send(method, f"/api{path}", body, headers)
        # A gateway in front of the app sends an expired login to the SSO page
        # (3xx) rather than answering 401; the transport never follows it.
        if status in (401, 403) or 300 <= status < 400:
            raise NeedsLogin()
        if status == 426:
            raise UpdateRequired(data)
        if status >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise BackendError(str(detail or f"HTTP {status}"))
        return data if status != 204 else None

    def heartbeat(self, **info: Any) -> None:
        self._call(
            "POST",
            "/outside-helper/heartbeat",
            {"version": __version__, "protocol": PROTOCOL, **info},
        )

    def claim(self) -> Job | None:
        data = self._call("POST", "/outside-helper/jobs/claim")
        if not data:
            return None
        return Job(id=data["id"], kind=data["kind"], args=data["args"])

    def result(self, job_id: str, *, title: str, url: str, markdown: str, date: str) -> None:
        self._call(
            "POST",
            f"/outside-helper/jobs/{job_id}/result",
            {"title": title, "url": url, "markdown": markdown, "date": date},
        )

    def fail(self, job_id: str, error: str) -> None:
        self._call("POST", f"/outside-helper/jobs/{job_id}/fail", {"error": error})

    def note(self, job_id: str, note: str) -> None:
        self._call("POST", f"/outside-helper/jobs/{job_id}/note", {"note": note})


__all__ = ["Backend", "BackendError", "Job", "NeedsLogin", "Send", "UpdateRequired"]
