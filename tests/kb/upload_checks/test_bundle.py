"""bundled_upload_checks() — the default registry the Ingestor wires
when no custom one is injected (#325).

Pins the v1 bundle end-to-end through the registry: an encrypted Office
file and a password-protected PDF are both refused; healthy files pass;
only the Office check (browser-runnable) contributes a client hint.
"""

from __future__ import annotations

import io

import pypdf
import pytest

from workspace_app.kb.upload_checks import UploadRejected, bundled_upload_checks

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _encrypted_pdf() -> bytes:
    w = pypdf.PdfWriter()
    w.add_blank_page(width=72, height=72)
    w.encrypt(user_password="secret")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_bundle_blocks_encrypted_office():
    r = bundled_upload_checks()
    with pytest.raises(UploadRejected) as exc:
        r.run(filename="deck.pptx", mime="application/x-ole-storage", data=OLE2 + b"blob")
    assert exc.value.rejection.check_id == "office_encryption"


def test_bundle_blocks_encrypted_pdf():
    r = bundled_upload_checks()
    with pytest.raises(UploadRejected) as exc:
        r.run(filename="report.pdf", mime="application/pdf", data=_encrypted_pdf())
    assert exc.value.rejection.check_id == "pdf_encryption"


def test_bundle_accepts_a_healthy_zip_office_file():
    r = bundled_upload_checks()
    r.run(filename="deck.pptx", mime="application/zip", data=b"PK\x03\x04 rest")


def test_bundle_exposes_only_the_office_client_hint():
    hints = bundled_upload_checks().hints()
    assert [h.id for h in hints] == ["office_encryption"]
