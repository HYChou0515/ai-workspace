"""The settings page's patch, as every app.json / profile reader sees it
(docs/plan-model-settings.md P5).

32 call sites read an App's manifest or a profile through `load_app_manifest` /
`load_profile`. Rather than teach each of them about the page, the two loaders
ask this module for the patch — so a reader added tomorrow is covered too. A
process registers where the published document comes from (`set_source`, at
boot, given `LiveSettings`); without one, the shipped files are read as they
always were (tests, tools that never boot the app).

Results are memoised by (version, name): the version string changes on every
Apply, so an unchanged version hands back the very object it built.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

#: Returns (published version, published document).
Source = Callable[[], tuple[str | None, dict[str, Any]]]

_source: Source | None = None
_memo: dict[tuple[Any, ...], Any] = {}
_memo_version: list[str | None] = [None]
_lock = threading.Lock()


def set_source(source: Source | None) -> None:
    global _source
    with _lock:
        _source = source
        _memo.clear()


def current() -> tuple[str | None, dict[str, Any]] | None:
    """The published (version, document), or None when no source is set."""
    source = _source
    return None if source is None else source()


def memoised(key: tuple[Any, ...], version: str | None, build: Callable[[], Any]) -> Any:
    """`build()`, kept for as long as the version stays the same."""
    with _lock:
        if _memo_version[0] != version:
            _memo.clear()
            _memo_version[0] = version
        if key in _memo:
            return _memo[key]
    value = build()
    with _lock:
        if _memo_version[0] == version:
            _memo[key] = value
    return value
