"""Pluggable upload checks (#325): synchronous gates that refuse
encrypted/unreadable files at upload time, before any SourceDoc is
created — with an actionable message instead of a background index error."""

from __future__ import annotations

from .office import OfficeEncryptionCheck
from .pdf import PdfEncryptionCheck
from .protocol import (
    IUploadCheck,
    UploadCheckHint,
    UploadRejected,
    UploadRejection,
)
from .registry import UploadCheckRegistry


def bundled_upload_checks() -> UploadCheckRegistry:
    """The default v1 gate: refuse encrypted/unreadable Office + PDF
    uploads. The Ingestor wires this when no custom registry is injected;
    an operator extends it by registering their own ``IUploadCheck``."""
    return UploadCheckRegistry().register(OfficeEncryptionCheck()).register(PdfEncryptionCheck())


__all__ = [
    "IUploadCheck",
    "OfficeEncryptionCheck",
    "PdfEncryptionCheck",
    "UploadCheckHint",
    "UploadCheckRegistry",
    "UploadRejected",
    "UploadRejection",
    "bundled_upload_checks",
]
