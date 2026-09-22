"""Backup and restore for this deployment's durable stores.

`docs/plan-backup.md` is the design. The short version: specstar's own
`dump()` / `load()` carry the API store (blob bytes travel inline, so an archive
is self-contained and nothing here has to understand specstar's on-disk layout),
the sandbox's workspace tree is copied as files, and both are cut into time
windows so a restore's memory is bounded by a slice rather than by the dataset.

Nothing in here goes over HTTP. The `/_backup/*` routes specstar registers are
fenced off in `api/app.py` — at 100 GB – 2 TB an HTTP export would buffer the
archive whole, and an HTTP import replaces the database from an uploaded file.
"""

from .run import RECEIPT_NAME, Receipt, SourceResult, run_backup
from .sources import DurableSource, SourceKind, UnsupportedDeployment, durable_sources

__all__ = [
    "RECEIPT_NAME",
    "DurableSource",
    "Receipt",
    "SourceKind",
    "SourceResult",
    "UnsupportedDeployment",
    "durable_sources",
    "run_backup",
]
