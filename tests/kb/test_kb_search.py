from collections.abc import Iterator

from agents import RunContextWrapper
from specstar import SpecStar

from workspace_app.agent import AgentToolContext, KbSearchBudget, kb_search_impl
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.llm import ILlm
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection, ContextCard


class _FakeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield "gamma", False


def _kb_ctx(spec: SpecStar, embedder: HashEmbedder, collection_ids: list[str]):
    return RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=collection_ids,
        )
    )


async def test_kb_search_returns_numbered_passages_and_fills_registry(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ctx = _kb_ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "reflow temperature")

    assert "[1]" in out  # numbered for the LLM to cite
    assert "reflow" in out
    # the passage is recorded so a later [n] in the answer maps back to a Citation
    assert len(ctx.context.kb_passages) == 1
    assert ctx.context.kb_passages[0].document_id == encode_doc_id(cid, "reflow.md")


async def test_kb_search_on_empty_returns_no_results_message(
    spec: SpecStar, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="empty")).resource_id
    ctx = _kb_ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "anything")

    assert "no" in out.lower()  # tells the agent nothing matched
    assert ctx.context.kb_passages == []


async def test_kb_search_streams_enhancement_thinking_to_the_run_sink(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # When the run wired an output sink, the retriever's enhancement-LLM work is
    # streamed to it (so it shows live under the kb_search tool card) — issue #10.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    captured: list[bytes] = []
    ctx = RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder, llm=_FakeLlm()),
            collection_ids=[cid],
            on_exec_output=captured.append,
        )
    )

    kb_search_impl(ctx, "gamma")

    text = b"".join(captured).decode()
    assert "↻ expanding query" in text  # step label streamed to the sink
    assert "gamma" in text  # the model's streamed chunk


def _capped_ctx(spec: SpecStar, embedder: HashEmbedder, collection_ids: list[str], cap: int | None):
    return RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=collection_ids,
            kb_search_budget=KbSearchBudget(max_calls=cap),
        )
    )


async def test_kb_search_appends_budget_footer_when_capped(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #195: when a per-turn cap is set, every result tells the model how much
    # of its search budget remains, so it spends searches frugally.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ctx = _capped_ctx(spec, embedder, [cid], cap=3)

    out = kb_search_impl(ctx, "reflow temperature")

    assert "1 of 3 used" in out  # this is the first search of the budget
    assert "2 left" in out
    assert "[1]" in out  # the passages are still returned alongside the footer


async def test_kb_search_no_footer_when_uncapped(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #195: Topic Hub etc. leave the cap None — no budget bookkeeping, no footer.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="reflow.md", data=b"reflow oven temperature"
    )
    ctx = _capped_ctx(spec, embedder, [cid], cap=None)

    out = kb_search_impl(ctx, "reflow temperature")

    assert "budget" not in out.lower()


async def test_kb_search_sentinel_when_budget_exhausted(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #195: the N+1th call doesn't run the retriever — it returns a sentinel
    # telling the model to answer from what it already has.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ctx = _capped_ctx(spec, embedder, [cid], cap=2)

    kb_search_impl(ctx, "reflow temperature")  # 1 of 2
    kb_search_impl(ctx, "solder voids")  # 2 of 2
    registry_len_at_cap = len(ctx.context.kb_passages)
    out = kb_search_impl(ctx, "zone three")  # exhausted — sentinel

    assert "budget" in out.lower() and "answer" in out.lower()
    assert "[" not in out  # no passages returned
    assert len(ctx.context.kb_passages) == registry_len_at_cap  # nothing new registered


async def test_kb_search_cap_zero_never_searches_and_steers_to_answer(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #334 Q4: a per-message cap of 0 means "don't search this reply". The very
    # first call must NOT run the retriever and must steer the model to answer
    # from context — and it reads as a deliberate "disabled", not "exhausted".
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="reflow.md", data=b"reflow oven temperature"
    )
    ctx = _capped_ctx(spec, embedder, [cid], cap=0)

    out = kb_search_impl(ctx, "reflow temperature")

    assert "[" not in out  # no passages — the retriever never ran
    assert ctx.context.kb_passages == []
    assert "no knowledge-base searches are allowed" in out.lower()
    assert "exhausted" not in out.lower()  # disabled, not used-up


async def test_kb_search_empty_result_still_consumes_budget(spec: SpecStar, embedder: HashEmbedder):
    # #195: a search that matches nothing still costs one unit of the budget,
    # so an empty-handed model can't loop forever re-searching.
    cid = spec.get_resource_manager(Collection).create(Collection(name="empty")).resource_id
    ctx = _capped_ctx(spec, embedder, [cid], cap=3)

    out = kb_search_impl(ctx, "anything")

    assert "no" in out.lower()  # still the no-results message
    assert "1 of 3 used" in out  # but the budget was consumed and reported


async def test_kb_search_keeps_numbers_stable_across_calls(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # the agentic case: the agent searches again and re-finds the same passage —
    # its citation number must not change, and it must not be double-registered.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ctx = _kb_ctx(spec, embedder, [cid])

    first = kb_search_impl(ctx, "reflow temperature")
    second = kb_search_impl(ctx, "temperature reflow")  # re-finds the same passage

    assert "[1]" in first and "[1]" in second  # same passage, same number
    assert len(ctx.context.kb_passages) == 1  # not double-registered


# --- #484: glossary auto-injection over retrieved passages -------------------


def _card(spec: SpecStar, cid: str, keys: list[str], *, title: str = "", body: str = "") -> str:
    rm = spec.get_resource_manager(ContextCard)
    card = ContextCard(
        collection_id=cid, keys=keys, norm_keys=derive_norm_keys(keys), title=title, body=body
    )
    return rm.create(card).resource_id


def _glossary_ctx(spec: SpecStar, embedder: HashEmbedder, collection_ids: list[str]):
    # Like `_kb_ctx` but wires `spec` so the glossary pre-scan can load cards.
    return RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=collection_ids,
            spec=spec,
        )
    )


async def test_kb_search_injects_glossary_for_a_term_in_a_retrieved_passage(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #484: when a retrieved passage contains a term that has a glossary card, the
    # authoritative definition is appended to the search result so the model uses
    # it instead of inventing a meaning.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="wafer.md",
        data=b"the reflow step deposits the capping layer over the metal stack",
    )
    _card(spec, cid, ["capping"], title="Capping layer", body="A protective dielectric cap.")
    ctx = _glossary_ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "capping layer")

    assert "Internal glossary entries" in out  # the authoritative block was appended
    assert "A protective dielectric cap." in out  # with the card body


async def test_kb_search_injects_each_card_only_once_per_turn(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #484: a card the turn already defined (an earlier search here) is not
    # re-injected when a later search re-surfaces the same term.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="wafer.md",
        data=b"the reflow step deposits the capping layer over the metal stack",
    )
    _card(spec, cid, ["capping"], title="Capping layer", body="A protective dielectric cap.")
    ctx = _glossary_ctx(spec, embedder, [cid])

    first = kb_search_impl(ctx, "capping layer")
    second = kb_search_impl(ctx, "the capping over metal")  # re-finds the same passage

    assert "A protective dielectric cap." in first  # defined on first sighting
    assert "A protective dielectric cap." not in second  # not repeated the second time


async def test_kb_search_skips_a_card_already_injected_upstream(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #484: the #106 user-message pre-scan seeds `injected_card_ids`; a card the
    # user's own question already pulled in is not injected again from a passage.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="wafer.md",
        data=b"the reflow step deposits the capping layer over the metal stack",
    )
    rid = _card(spec, cid, ["capping"], title="Capping layer", body="A protective dielectric cap.")
    ctx = _glossary_ctx(spec, embedder, [cid])
    ctx.context.injected_card_ids.add(rid)  # simulate the upstream #106 injection

    out = kb_search_impl(ctx, "capping layer")

    assert "[1]" in out  # passages still returned
    assert "A protective dielectric cap." not in out  # but the card is not re-injected


async def test_kb_search_without_glossary_cards_appends_nothing(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #484: no cards in the collection ⇒ result is exactly the passages (no block,
    # no spurious header) — the injection is invisible when there's nothing to add.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="wafer.md",
        data=b"the reflow step deposits the capping layer over the metal stack",
    )
    ctx = _glossary_ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "capping layer")

    assert "[1]" in out
    assert "glossary" not in out.lower()
