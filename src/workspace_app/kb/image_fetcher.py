"""IImageFetcher — pull an allowlisted internal image URL down to bytes (#513 P6).

HTML/MD knowledge often references its figures as external ``http`` links on an
internal image server. This is the seam that turns such a link into stored image
bytes.

The host allowlist (SSRF fence) is enforced HERE as well as by the caller's
``filter_allowed_urls`` pre-filter — defense in depth, so a caller that forgets
to filter still cannot reach an off-allowlist host. Any failure — off-allowlist,
network error, empty body — returns ``None`` rather than raising, so one broken
figure never fails the whole document's ingest.
"""

from __future__ import annotations

import abc
import logging
from collections.abc import Iterable
from urllib.request import urlopen

from .image_refs import filter_allowed_urls

logger = logging.getLogger(__name__)


class IImageFetcher(abc.ABC):
    @abc.abstractmethod
    def fetch(self, url: str) -> tuple[bytes, str] | None:
        """Return ``(image_bytes, mime)`` for ``url``, or ``None`` when it can't
        be fetched (off-allowlist, network error, empty body). Never raises for
        one bad URL — the caller skips a ``None`` and keeps ingesting the rest.
        """
        ...


class HttpImageFetcher(IImageFetcher):
    """Fetch over plain HTTP(S) with a host allowlist + timeout.

    Internal-network default: a straight GET (no auth). Auth is a future
    injectable seam (token/session) — the allowlist guard stays regardless.
    """

    def __init__(self, *, allowed_hosts: Iterable[str], timeout: float = 10.0) -> None:
        self._allowed_hosts = list(allowed_hosts)
        self._timeout = timeout

    def fetch(self, url: str) -> tuple[bytes, str] | None:
        # Defense-in-depth SSRF guard: re-run the allowlist even though the
        # Ingestor pre-filters, so a forgetful caller still can't reach off-list.
        if not filter_allowed_urls([url], allowed_hosts=self._allowed_hosts):
            logger.info("image fetch refused (off-allowlist): %s", url)
            return None
        try:
            with urlopen(url, timeout=self._timeout) as resp:  # noqa: S310 — allowlisted host only
                data = resp.read()
                mime = resp.headers.get_content_type()
        except Exception:  # noqa: BLE001 — one bad figure must not fail the ingest
            logger.warning("image fetch failed: %s", url, exc_info=True)
            return None
        if not data:
            return None
        return data, mime
