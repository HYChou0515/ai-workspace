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


#: (path, headers) -> (status, body bytes): a GET whose answer is a file.
FetchBytes = Callable[[str, dict[str, str]], tuple[int, bytes]]


def _headers() -> dict[str, str]:
    return {"X-Outside-Helper-Version": __version__, "X-Outside-Helper-Protocol": str(PROTOCOL)}


def _refusal(status: int, data: Any) -> None:
    # A gateway in front of the app sends an expired login to the SSO page
    # (3xx) rather than answering 401; the transport never follows it.
    # 403 is "not allowed" — signing in again would change nothing.
    if status == 401 or 300 <= status < 400:
        raise NeedsLogin()
    detail = data.get("detail") if isinstance(data, dict) else None
    if status == 426:
        # §3.7.3: the body names the current release to update to
        raise UpdateRequired(detail.get("release") if isinstance(detail, dict) else None)
    if status >= 400:
        raise BackendError(str(detail or f"HTTP {status}"))


class Backend:
    def __init__(self, send: Send, *, fetch_bytes: FetchBytes | None = None) -> None:
        self._send = send
        self._fetch_bytes = fetch_bytes

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, data = self._send(method, f"/api{path}", body, _headers())
        _refusal(status, data)
        return data if status != 204 else None

    def manifest(self) -> dict[str, Any] | None:
        """The release the operator published (§3.7.2), or None."""
        try:
            return self._call("GET", "/outside-helper/manifest")
        except BackendError:
            return None

    def download(self, file: str) -> bytes:
        """The release's zip — through the same signed-in line as the jobs, so
        it never carries a browser's "downloaded from the internet" mark."""
        if self._fetch_bytes is None:
            raise BackendError("this transport cannot fetch files")
        status, data = self._fetch_bytes(f"/api/outside-helper/download/{file}", _headers())
        _refusal(status, None)
        return data

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

    def lookup_targets(self) -> list[dict[str, str]]:
        """The card's search destinations (`server.lookup_targets`)."""
        data = self._call("GET", "/lookup-targets")
        return [t for t in data or [] if isinstance(t, dict)]

    def note(self, job_id: str, note: str) -> None:
        self._call("POST", f"/outside-helper/jobs/{job_id}/note", {"note": note})


__all__ = ["Backend", "BackendError", "FetchBytes", "Job", "NeedsLogin", "Send", "UpdateRequired"]
