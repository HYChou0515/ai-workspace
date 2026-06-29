"""OfficeEncryptionCheck — refuse encrypted/unreadable OOXML uploads.

A healthy .pptx/.xlsx/.docx is a ZIP container. Password-protecting one
re-wraps the whole file in an OLE2/CFB compound container (the same
``D0 CF 11 E0 A1 B1 1A E1`` signature legacy binary Office used), which
the OOXML parsers can't open. Detecting that is a leading-byte compare —
cheap, and identical to what the browser can do — so this check is the
shared FE/BE baseline (it also exposes a ``client_prefilter`` hint).

It keys off the file *extension*: an encrypted file's libmagic-sniffed
mime is no longer the OOXML mime, so mime can't tell us "this was meant
to be an Office doc" — the extension can.
"""

from __future__ import annotations

from .protocol import (
    UNREADABLE_MESSAGE_KEY,
    IUploadCheck,
    UploadCheckHint,
    UploadRejection,
)

#: OLE2/CFB compound-file signature. An OOXML upload starting with this is
#: encrypted (or a mislabelled legacy binary) — either way unreadable.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: OOXML extensions this check guards (lower-case, dot-prefixed).
_OOXML_EXTENSIONS = (".pptx", ".xlsx", ".docx")


class OfficeEncryptionCheck(IUploadCheck):
    @property
    def id(self) -> str:
        return "office_encryption"

    def applies(self, *, filename: str, mime: str) -> bool:
        return filename.lower().endswith(_OOXML_EXTENSIONS)

    def inspect(self, data: bytes) -> UploadRejection | None:
        if data.startswith(_OLE2_MAGIC):
            return UploadRejection(
                check_id=self.id,
                reason_code="encrypted_office",
                message_key=UNREADABLE_MESSAGE_KEY,
            )
        return None

    def client_prefilter(self) -> UploadCheckHint | None:
        return UploadCheckHint(
            id=self.id,
            extensions=_OOXML_EXTENSIONS,
            forbid_magic_hex=(_OLE2_MAGIC.hex(),),
            message_key=UNREADABLE_MESSAGE_KEY,
        )
