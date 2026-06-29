"""OfficeEncryptionCheck — block encrypted/unreadable OOXML uploads (#325).

A normal .pptx/.xlsx/.docx is a ZIP container (leading bytes ``PK``).
When the file is password-protected, the WHOLE file is wrapped in an
OLE2/CFB compound container instead (leading bytes ``D0 CF 11 E0 ...``),
which the OOXML parsers can't open. The check keys off the *extension*
(an encrypted file's sniffed mime is no longer the OOXML mime, so mime
is unreliable) and rejects when the container is OLE2.

This is the SAME magic-byte rule the FE runs in the browser via the
``client_prefilter`` hint, so FE pre-block and BE authoritative-block
never disagree.
"""

from __future__ import annotations

from workspace_app.kb.upload_checks import UploadRejection
from workspace_app.kb.upload_checks.office import OfficeEncryptionCheck

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # encrypted OOXML / legacy compound
ZIP = b"PK\x03\x04"  # a healthy OOXML file


def test_applies_to_ooxml_extensions_case_insensitively():
    c = OfficeEncryptionCheck()
    assert c.applies(filename="deck.pptx", mime="application/zip")
    assert c.applies(filename="BOOK.XLSX", mime="application/zip")
    assert c.applies(filename="memo.docx", mime="application/octet-stream")


def test_does_not_apply_to_other_types():
    c = OfficeEncryptionCheck()
    assert not c.applies(filename="report.pdf", mime="application/pdf")
    assert not c.applies(filename="notes.txt", mime="text/plain")
    assert not c.applies(filename="data.csv", mime="text/csv")


def test_rejects_an_ole2_wrapped_office_file():
    c = OfficeEncryptionCheck()
    rej = c.inspect(OLE2 + b"rest of the encrypted blob")
    assert isinstance(rej, UploadRejection)
    assert rej.check_id == c.id
    assert rej.reason_code == "encrypted_office"
    assert rej.message_key  # carries an i18n key, not a raw exception string


def test_accepts_a_normal_zip_office_file():
    c = OfficeEncryptionCheck()
    assert c.inspect(ZIP + b"...the rest of a valid pptx...") is None


def test_client_prefilter_mirrors_the_server_rule():
    c = OfficeEncryptionCheck()
    hint = c.client_prefilter()
    assert hint is not None
    assert set(hint.extensions) == {".pptx", ".xlsx", ".docx"}
    # The forbidden magic is the OLE2 signature, as hex — the FE compares
    # a picked file's leading bytes against it.
    assert hint.forbid_magic_hex == ("d0cf11e0a1b11ae1",)
    # Same copy on both rejection paths.
    rej = c.inspect(OLE2)
    assert rej is not None
    assert hint.message_key == rej.message_key
