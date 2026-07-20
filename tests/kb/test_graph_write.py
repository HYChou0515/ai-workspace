from collections.abc import Iterator

from specstar import QB

from workspace_app.kb.graph.write import norm_metric, write_doc_claims
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield self._reply, False


def _claims(spec, doc: str) -> list[GraphClaim]:
    rm = spec.get_resource_manager(GraphClaim)
    out: list[GraphClaim] = []
    for r in rm.list_resources((QB["source_doc_id"] == doc).build()):
        assert isinstance(r.data, GraphClaim)
        out.append(r.data)
    return out


def test_norm_metric_collapses_whitespace_and_casefolds():
    assert norm_metric("  Net   Income ") == "net income"
    assert norm_metric("營收") == "營收"


def test_write_doc_claims_persists_with_norm_and_provenance():
    llm = _FakeLlm(
        '[{"metric": "Revenue", "period": "Q3", "value": "1.2M", "unit": "USD"},'
        ' {"metric": "Head Count", "value": "340"}]'
    )
    spec = make_spec()
    n = write_doc_claims(
        spec, llm, collection_id="c1", source_doc_id="deck-A", chunks=[("deck-A#0", "t")]
    )
    assert n == 2
    claims = _claims(spec, "deck-A")
    assert {c.metric for c in claims} == {"Revenue", "Head Count"}
    assert {c.norm_metric for c in claims} == {"revenue", "head count"}
    assert all(c.collection_id == "c1" and c.chunk_id == "deck-A#0" for c in claims)


def test_write_doc_claims_is_idempotent_wipe_then_rewrite():
    spec = make_spec()
    write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "x")],
    )
    write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.3M"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "x")],
    )
    claims = _claims(spec, "deck-A")
    assert len(claims) == 1  # wiped + rewritten, never doubled
    assert claims[0].value == "1.3M"  # the re-run's value won
