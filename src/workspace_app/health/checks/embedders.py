"""Embedder probes — fast (connectivity-grade): one real embed call,
assert the vector width matches what DocChunk storage expects. A dim
mismatch poisons EVERY ingest, so this runs synchronously at startup.
"""

from __future__ import annotations

from ...kb.embedder import Embedder
from ..protocol import CheckResult, ISanityCheck


class EmbedderDimCheck(ISanityCheck):
    fast = True

    def __init__(
        self,
        embedder: Embedder | None,
        *,
        expected_dim: int,
        check_id: str,
        description: str,
    ) -> None:
        self._embedder = embedder
        self._expected_dim = expected_dim
        self.check_id = check_id
        self.description = description

    def run(self) -> CheckResult:
        if self._embedder is None:
            return CheckResult(check_id=self.check_id, status="skip", detail="not configured")
        vec = self._embedder.embed_documents(["sanity probe"])[0]
        if len(vec) == self._expected_dim:
            return CheckResult(check_id=self.check_id, status="pass", detail=f"dim {len(vec)}")
        return CheckResult(
            check_id=self.check_id,
            status="fail",
            detail=f"embedding dim {len(vec)} != expected {self._expected_dim} — "
            f"chunks would be unsearchable; fix the model or re-index",
        )
