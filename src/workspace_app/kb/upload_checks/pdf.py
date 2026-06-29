"""PdfEncryptionCheck — refuse password-protected PDFs.

An encrypted PDF still starts with ``%PDF`` (the encryption is in the
trailer), so unlike the Office check there's no magic-byte shortcut — we
must parse it. This check is therefore server-only (no client prefilter):
the browser can't tell, so the FE relies on the upload 422.

We block only what the system genuinely can't read: ``is_encrypted`` AND
an empty password fails. A permissions-only PDF (owner password, empty
user password) stays readable, so it's accepted — over-blocking those
would refuse perfectly usable documents. PDFs pypdf can't open at all
aren't an encryption case; they fall through to the existing
background-index error path rather than a hard upload rejection.
"""

from __future__ import annotations

import io

import pypdf

from .protocol import (
    UNREADABLE_MESSAGE_KEY,
    IUploadCheck,
    UploadRejection,
)


class PdfEncryptionCheck(IUploadCheck):
    @property
    def id(self) -> str:
        return "pdf_encryption"

    def applies(self, *, filename: str, mime: str) -> bool:
        return filename.lower().endswith(".pdf")

    def inspect(self, data: bytes) -> UploadRejection | None:
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            if not reader.is_encrypted:
                return None
            # is_encrypted covers permissions-only PDFs too; only the ones
            # an empty password can't open are truly unreadable.
            opened = reader.decrypt("") != pypdf.PasswordType.NOT_DECRYPTED
        except Exception:  # noqa: BLE001 — corrupt/odd PDFs aren't this gate's job
            return None
        if opened:
            return None
        return UploadRejection(
            check_id=self.id,
            reason_code="encrypted_pdf",
            message_key=UNREADABLE_MESSAGE_KEY,
        )
