"""MaterialisedParserInput — bytes-backed concrete IParserInput.

The Ingestor reads a SourceDoc's blob into memory then wraps it in a
``MaterialisedParserInput`` for ``parser.parse(...)``. The adapter:

  - returns the SAME ``bytes`` instance from every ``as_bytes()`` call
    (no copy / no re-read);
  - writes the bytes to a tempfile on the first ``as_path()`` call,
    caches the path, and returns the SAME path thereafter;
  - returns a FRESH ``BytesIO`` from every ``as_stream()`` call so two
    callers don't race on the cursor.
  - closes (unlinks tempfile) via ``close()`` / context manager so
    the file doesn't leak across investigations.
"""

from __future__ import annotations

from pathlib import Path

from workspace_app.kb.parsers.input import MaterialisedParserInput


def test_as_bytes_returns_the_exact_bytes_object_each_call():
    src = MaterialisedParserInput(b"reflow temperature drift")
    a = src.as_bytes()
    b = src.as_bytes()
    assert a == b"reflow temperature drift"
    # Same identity — no defensive copy on read (the bytes are
    # immutable anyway; copy would just waste memory).
    assert a is b


def test_as_path_materialises_a_tempfile_on_first_call_and_caches_it():
    src = MaterialisedParserInput(b"some content")
    p1 = src.as_path()
    p2 = src.as_path()
    assert isinstance(p1, Path)
    assert p1 == p2
    # The file exists on disk with the expected bytes.
    assert p1.is_file()
    assert p1.read_bytes() == b"some content"


def test_as_path_uses_filename_suffix_when_supplied():
    """Some libraries dispatch on extension (`pdfplumber` reads
    `.pdf`; pandas `read_csv` is more relaxed but still picks up
    `.csv`). Surface the original filename's suffix on the tempfile."""
    src = MaterialisedParserInput(b"a,b\n1,2\n", filename="data.csv")
    p = src.as_path()
    assert p.suffix == ".csv"


def test_as_stream_returns_a_fresh_stream_each_call():
    src = MaterialisedParserInput(b"abcdef")
    s1 = src.as_stream()
    assert s1.read(3) == b"abc"
    # A second caller asks for the stream — it's positioned at 0, not
    # at byte 3 (the first stream's cursor).
    s2 = src.as_stream()
    assert s2.read() == b"abcdef"


def test_close_removes_the_materialised_tempfile():
    src = MaterialisedParserInput(b"x")
    p = src.as_path()
    assert p.is_file()
    src.close()
    assert not p.exists()


def test_close_is_idempotent_when_no_tempfile_was_materialised():
    src = MaterialisedParserInput(b"x")
    # never called as_path() → nothing on disk to clean. close()
    # must not raise.
    src.close()


def test_context_manager_closes_on_exit():
    with MaterialisedParserInput(b"x") as src:
        p = src.as_path()
        assert p.is_file()
    assert not p.exists()


def test_as_path_cleans_up_the_tempfile_when_the_write_fails(monkeypatch, tmp_path):
    """A failed materialise (disk full, fd error) must not leak the
    half-written tempfile — the adapter unlinks it and re-raises."""
    import os
    from pathlib import Path

    import workspace_app.kb.parsers.input as input_mod
    from workspace_app.kb.parsers.input import MaterialisedParserInput

    created: list[str] = []
    real_mkstemp = input_mod.tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(name)
        return fd, name

    def failing_fdopen(fd, *args, **kwargs):
        os.close(fd)
        raise OSError("disk full")

    monkeypatch.setattr(input_mod.tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(input_mod.os, "fdopen", failing_fdopen)

    src = MaterialisedParserInput(b"payload", filename="x.bin")
    import pytest as _pytest

    with _pytest.raises(OSError, match="disk full"):
        src.as_path()
    (name,) = created
    assert not Path(name).exists()  # cleaned up, not leaked
