"""The proxy must not reject uploads the app is configured to accept.

`FilestoreSettings.max_file_size` defaults to 2 GiB, but ingress-nginx defaults
`proxy-body-size` to **1 MB** — so without an explicit annotation the proxy
rejected anything larger with a 413 that never reached the app. A few-MB
attachment came back as "exceeds the size limit" while the server logs showed
nothing at all, because the request was killed two thousand times below the
limit the app believed it was enforcing.

This ties the two numbers together so they cannot drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from workspace_app.config.schema import FilestoreSettings

_INGRESS = Path(__file__).resolve().parents[1] / "kubernetes" / "base" / "ingress.yaml"
_ANNOTATION = "nginx.ingress.kubernetes.io/proxy-body-size"


def _to_bytes(size: str) -> int:
    """Parse an nginx size (`1m`, `2g`, `512k`, or plain bytes)."""
    m = re.fullmatch(r"(\d+)([kmg]?)", size.strip().lower())
    assert m is not None, f"unparseable proxy-body-size: {size!r}"
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2)]


def _annotations() -> dict[str, str]:
    doc = yaml.safe_load(_INGRESS.read_text())
    return doc["metadata"]["annotations"]


def test_the_proxy_accepts_at_least_what_the_app_accepts() -> None:
    ann = _annotations()
    assert _ANNOTATION in ann, (
        "ingress-nginx defaults proxy-body-size to 1 MB, so leaving it unset "
        "rejects almost every real attachment before it reaches the app"
    )
    assert _to_bytes(ann[_ANNOTATION]) >= FilestoreSettings().max_file_size


def test_the_upload_body_is_streamed_rather_than_buffered() -> None:
    """The upload handler consumes `request.stream()` and enforces the size cap
    incrementally, so it can reject a too-large file early. Buffering the whole
    body in the proxy first defeats that — a 2 GiB upload would be spooled to the
    proxy's disk in full before the app is allowed to see (and refuse) a single
    byte of it.
    """
    assert _annotations().get("nginx.ingress.kubernetes.io/proxy-request-buffering") == "off"
