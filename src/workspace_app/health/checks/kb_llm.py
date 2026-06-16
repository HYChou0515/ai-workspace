"""KB-LLM capability probes — the qwen3:14b incident class.

Both probes drive the REAL feature code path (InsightExtractor /
expand_queries) against the live model with a tiny canned input and a
functional assertion. Connectivity is not the question — "can this
model do the task" is.
"""

from __future__ import annotations

from ...kb.llm import ILlm
from ..protocol import CheckResult, ISanityCheck

_MINI_CONVERSATION = [
    {"role": "user", "content": "Defect counts spiked on lot 25-W14 after the reflow oven."},
    {
        "role": "assistant",
        "content": "Zone-3 thermocouple was found 8°C off; recalibration fixed the spike. "
        "Root cause confirmed: zone-3 thermocouple drift.",
    },
    {"role": "user", "content": "Great, document it."},
]


class InsightExtractionCheck(ISanityCheck):
    check_id = "insight-extraction"
    description = "Distill a tiny known conversation into at least one insight"

    def __init__(self, llm: ILlm | None) -> None:
        self._llm = llm

    def run(self) -> CheckResult:
        from ...kb.insight_extractor import InsightExtractor, conversation_to_extraction_doc

        if self._llm is None:
            return CheckResult(check_id=self.check_id, status="skip", detail="not configured")
        doc = conversation_to_extraction_doc(
            investigation_id="sanity-probe", title="sanity probe", messages=_MINI_CONVERSATION
        )
        nodes = InsightExtractor(llm=self._llm)([doc])
        if nodes:
            kinds = sorted({str(n.metadata.get("kind", "")) for n in nodes})
            return CheckResult(
                check_id=self.check_id,
                status="pass",
                detail=f"{len(nodes)} insight(s): {', '.join(kinds)}",
            )
        return CheckResult(
            check_id=self.check_id,
            status="fail",
            detail="the model produced no parseable insights from a conversation "
            "with an explicit confirmed root cause — chat ingestion would be empty",
        )


class RetrievalExpandCheck(ISanityCheck):
    check_id = "retrieval-expand"
    description = "Generate alternative query phrasings for retrieval"

    def __init__(self, llm: ILlm | None) -> None:
        self._llm = llm

    def run(self) -> CheckResult:
        from ...kb.query import expand_queries

        if self._llm is None:
            return CheckResult(check_id=self.check_id, status="skip", detail="not configured")
        out = expand_queries(self._llm, "wafer defect root cause", n=2)
        # expand_queries always returns the original first; capability =
        # at least one usable alternative came back.
        if len(out) >= 2:
            return CheckResult(
                check_id=self.check_id, status="pass", detail=f"{len(out) - 1} alternative(s)"
            )
        return CheckResult(
            check_id=self.check_id,
            status="fail",
            detail="the model produced no alternative phrasings — multi-query / "
            "HyDE retrieval enhancements would silently degrade",
        )
