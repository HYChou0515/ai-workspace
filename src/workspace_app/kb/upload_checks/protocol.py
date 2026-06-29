"""IUploadCheck + verdict/hint value objects — the contract every
upload gate implements (#325).

Per the project's interface convention (ABC over Protocol): an ABC so a
subclass that forgets ``applies`` / ``inspect`` blows up at construction,
not at the first upload; interface and impls live in separate modules
(``office.py`` / ``pdf.py``, the registry in ``registry.py``).

A check is the pluggable "custom check" unit the operator extends: add a
subclass + register it. The bundled v1 set blocks encrypted/unreadable
Office + PDF files (an encrypted OOXML file is no longer a ZIP container,
a password-protected PDF can't be read) so the upload is refused with an
actionable message instead of silently failing in the background index.

Two halves, deliberately split:

  - ``inspect(data) -> UploadRejection | None`` is the **authoritative**
    server-side verdict (pure detection, no control flow — it returns a
    verdict, the registry decides to raise). It can do real work the
    browser can't (parse a PDF trailer).
  - ``client_prefilter() -> UploadCheckHint | None`` is the optional,
    **declarative** subset the FE can run in the browser before upload
    (a magic-byte prefix rule). The FE fetches these via
    ``GET /kb/upload-checks`` so the common case is blocked instantly,
    without a round-trip — and a new browser-checkable rule is added by
    the check declaring a hint, with zero FE code change. A check whose
    detection needs real parsing returns ``None`` and stays server-only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# The i18n key shown for an encrypted/unreadable file. One shared key
# keeps the copy honest + identical across formats: we can't always tell
# encryption from corruption from the container alone, so the message
# says "can't read — if it's password-protected, decrypt and re-upload"
# rather than over-claiming "encrypted". The FE owns the actual wording.
UNREADABLE_MESSAGE_KEY = "kb.upload.blocked.unreadable"


@dataclass(frozen=True)
class UploadRejection:
    """A check's verdict that a file must NOT be ingested.

    Carries a stable ``reason_code`` (for logs / future programmatic use)
    and an i18n ``message_key`` the FE localises — never a raw library
    exception string (UI copy: describe the outcome, keep formats/system
    nouns out of user-facing text). ``check_id`` names which check fired.
    """

    check_id: str
    reason_code: str
    message_key: str


@dataclass(frozen=True)
class UploadCheckHint:
    """The declarative, browser-runnable part of a check.

    The FE reads the leading bytes of a picked file and rejects it when
    its extension is in ``extensions`` AND its leading bytes match any
    prefix in ``forbid_magic_hex``. ``message_key`` is the same i18n key
    the server-side ``UploadRejection`` carries, so both rejection paths
    show identical copy.
    """

    id: str
    extensions: tuple[str, ...]
    forbid_magic_hex: tuple[str, ...]
    message_key: str


class IUploadCheck(ABC):
    """One pluggable upload gate. Run synchronously in ``Ingestor.store``
    before any SourceDoc is created."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Stable identifier (e.g. ``"office_encryption"``) — names the
        check in its ``UploadRejection`` and ``UploadCheckHint``."""

    @abstractmethod
    def applies(self, *, filename: str, mime: str) -> bool:
        """``True`` when this check should inspect the file. Keyed off
        cheap signals (extension / sniffed mime) so ``inspect`` only runs
        on candidates — e.g. the Office check applies to ``.pptx`` /
        ``.xlsx`` / ``.docx`` by extension (an encrypted file's sniffed
        mime is NOT the OOXML mime, so extension is the reliable key)."""

    @abstractmethod
    def inspect(self, data: bytes) -> UploadRejection | None:
        """Authoritative verdict on the bytes: ``None`` to accept, an
        ``UploadRejection`` to block. Pure — no raise, no side effects —
        so it's trivially unit-testable; the registry turns a rejection
        into the ``UploadRejected`` control-flow signal."""

    def client_prefilter(self) -> UploadCheckHint | None:
        """The browser-runnable subset of this check, or ``None`` when
        the detection needs real parsing the browser can't do (PDF). The
        default is ``None`` — server-only."""
        return None


class UploadRejected(Exception):
    """Raised by the registry when a check rejects an upload. Carries the
    triggering ``UploadRejection`` so the API layer can map it to an HTTP
    422 with the structured ``{check_id, reason_code, message_key}`` body
    the FE localises."""

    def __init__(self, rejection: UploadRejection) -> None:
        self.rejection = rejection
        super().__init__(f"{rejection.check_id}: {rejection.reason_code}")
