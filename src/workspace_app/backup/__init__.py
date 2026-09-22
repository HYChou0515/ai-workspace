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

from .ledger import BackupLedger
from .restore import CoverageMismatch, RestoreReport, restore_chain
from .run import RECEIPT_NAME, Receipt, SourceResult, chain_of, run_backup
from .sources import DurableSource, SourceKind, UnsupportedDeployment, durable_sources
from .staleness import sweep_backup_staleness
from .verify import IncompleteArchive, verify_archives

__all__ = [
    "RECEIPT_NAME",
    "BackupLedger",
    "CoverageMismatch",
    "DurableSource",
    "IncompleteArchive",
    "Receipt",
    "RestoreReport",
    "SourceKind",
    "SourceResult",
    "UnsupportedDeployment",
    "chain_of",
    "durable_sources",
    "restore_chain",
    "run_backup",
    "sweep_backup_staleness",
    "verify_archives",
]
