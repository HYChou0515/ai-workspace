"""UploadCheckRegistry — synchronous upload gate (#325).

The Ingestor holds ONE ``UploadCheckRegistry`` and calls
``run(filename, mime, data)`` at the top of ``store()`` — BEFORE any
SourceDoc is created or any archive is expanded. Each registered
``IUploadCheck`` whose ``applies(filename, mime)`` is True gets to
``inspect(data)``; the first that returns an ``UploadRejection`` aborts
the upload by raising ``UploadRejected`` (mapped to HTTP 422 upstream).

These tests pin the REGISTRY shape with injected stub checks, not any
real encryption-detection rule (those live in the office/pdf checks).
"""

from __future__ import annotations

from collections.abc import Callable

from workspace_app.kb.upload_checks import (
    IUploadCheck,
    UploadCheckHint,
    UploadCheckRegistry,
    UploadRejected,
    UploadRejection,
)


class _StubCheck(IUploadCheck):
    """Delegates ``applies`` / ``inspect`` to injected callables so a
    test can pin exactly when the check fires and what it returns."""

    def __init__(
        self,
        *,
        id: str,
        applies: Callable[[str, str], bool],
        verdict: Callable[[bytes], UploadRejection | None],
        hint: UploadCheckHint | None = None,
    ) -> None:
        self._id = id
        self._applies = applies
        self._verdict = verdict
        self._hint = hint

    @property
    def id(self) -> str:
        return self._id

    def applies(self, *, filename: str, mime: str) -> bool:
        return self._applies(filename, mime)

    def inspect(self, data: bytes) -> UploadRejection | None:
        return self._verdict(data)

    def client_prefilter(self) -> UploadCheckHint | None:
        return self._hint


def _reject(id: str) -> Callable[[bytes], UploadRejection | None]:
    return lambda _data: UploadRejection(check_id=id, reason_code="nope", message_key="k")


def test_empty_registry_accepts_any_upload():
    r = UploadCheckRegistry()
    # No raise == accepted.
    r.run(filename="x.pptx", mime="application/zip", data=b"anything")


def test_applicable_rejecting_check_aborts_the_upload():
    import pytest

    r = UploadCheckRegistry().register(
        _StubCheck(id="enc", applies=lambda f, m: True, verdict=_reject("enc"))
    )
    with pytest.raises(UploadRejected) as exc:
        r.run(filename="x.pptx", mime="application/zip", data=b"PK")
    assert exc.value.rejection.check_id == "enc"
    assert exc.value.rejection.message_key == "k"


def test_check_that_does_not_apply_is_skipped():
    """``inspect`` never runs for a non-applicable check — so an upload
    only the PDF check applies to never pays the Office check's cost and
    vice-versa."""
    inspected: list[bytes] = []

    def _record(data: bytes) -> UploadRejection | None:
        inspected.append(data)
        return UploadRejection(check_id="x", reason_code="nope", message_key="k")

    r = UploadCheckRegistry().register(
        _StubCheck(id="x", applies=lambda f, m: False, verdict=_record)
    )
    r.run(filename="x.pdf", mime="application/pdf", data=b"%PDF")
    assert inspected == []


def test_first_rejection_wins_and_short_circuits():
    """One reason is enough to refuse — later checks aren't consulted."""
    import pytest

    later_ran: list[bytes] = []
    r = (
        UploadCheckRegistry()
        .register(_StubCheck(id="first", applies=lambda f, m: True, verdict=_reject("first")))
        .register(
            _StubCheck(
                id="second",
                applies=lambda f, m: True,
                verdict=lambda d: later_ran.append(d) or None,  # type: ignore[func-returns-value]
            )
        )
    )
    with pytest.raises(UploadRejected) as exc:
        r.run(filename="x.pptx", mime="application/zip", data=b"data")
    assert exc.value.rejection.check_id == "first"
    assert later_ran == []


def test_accepting_check_lets_the_upload_through():
    r = UploadCheckRegistry().register(
        _StubCheck(id="ok", applies=lambda f, m: True, verdict=lambda d: None)
    )
    r.run(filename="x.pptx", mime="application/zip", data=b"PK\x03\x04")


def test_hints_collects_only_checks_with_a_client_prefilter():
    hint = UploadCheckHint(
        id="office", extensions=(".pptx",), forbid_magic_hex=("d0cf11e0",), message_key="k"
    )
    r = (
        UploadCheckRegistry()
        .register(
            _StubCheck(id="office", applies=lambda f, m: True, verdict=lambda d: None, hint=hint)
        )
        .register(_StubCheck(id="pdf", applies=lambda f, m: True, verdict=lambda d: None))
    )
    assert r.hints() == [hint]


def test_checks_lists_registrations_in_order():
    a = _StubCheck(id="a", applies=lambda f, m: False, verdict=lambda d: None)
    b = _StubCheck(id="b", applies=lambda f, m: False, verdict=lambda d: None)
    r = UploadCheckRegistry().register(a).register(b)
    assert r.checks() == [a, b]
