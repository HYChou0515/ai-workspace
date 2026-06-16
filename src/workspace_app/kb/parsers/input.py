"""Concrete ``IParserInput`` backed by an in-memory ``bytes`` blob.

The Ingestor reads a SourceDoc's blob via specstar
``restore_binary(...)`` into Python bytes (uploads are bounded by the
KB's max upload size), then hands the bytes to a parser through this
adapter. The adapter takes care of:

  - returning the same bytes object on every ``as_bytes()`` call so a
    parser that calls it twice doesn't pay extra memory;
  - materialising the bytes into a tempfile ONLY the first time a
    parser calls ``as_path()``, then caching the path so a parser
    that wants the path multiple times still ends up with one file;
  - returning a fresh ``BinaryIO`` from every ``as_stream()`` call so
    two callers (or two passes) don't race on the cursor.

Used as a context manager when the caller wants RAII-style tempfile
cleanup; otherwise call ``close()`` explicitly. ``close()`` is
idempotent and safe to call when no tempfile was ever materialised.
"""

from __future__ import annotations

import io
import os
import posixpath
import tempfile
from pathlib import Path
from typing import BinaryIO

from .protocol import IParserInput


class MaterialisedParserInput(IParserInput):
    """Bytes-backed ``IParserInput`` for the Ingestor's normal path.

    ``filename`` is optional metadata used only to give the tempfile a
    matching suffix (so a parser that dispatches on extension —
    pdfplumber, pandas — sees ``/tmp/xyz.csv`` rather than
    ``/tmp/xyz``). Parsers should read the authoritative filename from
    the ``parse()`` keyword argument the registry passes them, NOT
    from this tempfile path.
    """

    def __init__(self, data: bytes, *, filename: str | None = None) -> None:
        self._data = data
        self._suffix = posixpath.splitext(filename)[1] if filename else ""
        self._path: Path | None = None

    def as_bytes(self) -> bytes:
        return self._data

    def as_path(self) -> Path:
        if self._path is None:
            fd, name = tempfile.mkstemp(suffix=self._suffix)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(self._data)
            except BaseException:
                Path(name).unlink(missing_ok=True)
                raise
            self._path = Path(name)
        return self._path

    def as_stream(self) -> BinaryIO:
        return io.BytesIO(self._data)

    def close(self) -> None:
        """Remove any tempfile ``as_path()`` materialised. Idempotent
        — calling it twice (or when no path was materialised) is a
        no-op."""
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None

    def __enter__(self) -> MaterialisedParserInput:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
