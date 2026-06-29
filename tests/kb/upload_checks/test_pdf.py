"""PdfEncryptionCheck — refuse password-protected PDFs (#325).

Unlike Office files, an encrypted PDF still starts with ``%PDF`` — the
encryption lives in the trailer, so the only reliable signal is parsing
it. This check is therefore SERVER-ONLY (no ``client_prefilter``): the
browser can't tell, so the FE relies on the 422.

It blocks only PDFs the system genuinely can't read: ``is_encrypted`` AND
an empty password fails to unlock it. A permissions-only PDF (owner
password, empty *user* password) IS readable, so it's accepted — we
don't over-block.

Encrypted/plain fixtures are synthesised in-process with pypdf (the same
lib the parser uses), so no binary blobs land in the repo.
"""

from __future__ import annotations

import io

import pypdf

from workspace_app.kb.upload_checks import UploadRejection
from workspace_app.kb.upload_checks.pdf import PdfEncryptionCheck


def _pdf(*, user_password: str | None = None, owner_password: str | None = None) -> bytes:
    w = pypdf.PdfWriter()
    w.add_blank_page(width=72, height=72)
    if user_password is not None or owner_password is not None:
        w.encrypt(user_password=user_password or "", owner_password=owner_password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_applies_to_pdf_extension_case_insensitively():
    c = PdfEncryptionCheck()
    assert c.applies(filename="report.pdf", mime="application/pdf")
    assert c.applies(filename="REPORT.PDF", mime="application/pdf")
    assert not c.applies(filename="deck.pptx", mime="application/zip")


def test_rejects_a_password_protected_pdf():
    c = PdfEncryptionCheck()
    rej = c.inspect(_pdf(user_password="secret"))
    assert isinstance(rej, UploadRejection)
    assert rej.check_id == c.id
    assert rej.reason_code == "encrypted_pdf"
    assert rej.message_key


def test_accepts_a_plain_pdf():
    c = PdfEncryptionCheck()
    assert c.inspect(_pdf()) is None


def test_accepts_a_permissions_only_pdf_with_empty_user_password():
    """Owner-password-only encryption leaves the document readable with an
    empty user password — the system CAN read it, so don't block it."""
    c = PdfEncryptionCheck()
    assert c.inspect(_pdf(user_password="", owner_password="owner")) is None


def test_corrupt_pdf_bytes_are_not_our_concern():
    """A .pdf that pypdf can't even open isn't an encryption case — leave
    it to the existing background-index error path, don't hard-block here."""
    c = PdfEncryptionCheck()
    assert c.inspect(b"%PDF-1.4 not really a pdf") is None


def test_no_client_prefilter_pdf_is_server_only():
    assert PdfEncryptionCheck().client_prefilter() is None
