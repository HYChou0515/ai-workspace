"""#534 B — pull the things a passage talks about, verbatim.

The extractor's only job is to say WHAT the text mentions and, in the text's own
words, what kind of thing it is. It does not decide whether two mentions are the
same thing, does not normalise, and does not filter by kind — every one of those
is a later, separate decision made against accumulated evidence rather than one
passage at a time.

Two consequences show up in these tests. The surface is kept EXACTLY as written,
because the raw form is the evidence and everything downstream derives from it.
And the kind is free text, not a fixed list: the useful kinds are domain-specific
(a manufacturing corpus wants 機台 / 製程 / 缺陷, not the categories a general
model would guess), so the taxonomy has to come out of the data rather than be
imposed on it — the kind labels get unified by the same mechanism as everything
else.
"""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.graph.entity_extract import EntityMention, extract_entities
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield self._reply, False


def test_extracts_surface_and_kind():
    llm = _FakeLlm(
        '[{"surface": "回焊爐", "kind": "機台"}, {"surface": "錫膏印刷", "kind": "製程"}]'
    )
    assert extract_entities(llm, "…") == [
        EntityMention(surface="回焊爐", kind="機台"),
        EntityMention(surface="錫膏印刷", kind="製程"),
    ]


def test_the_surface_is_kept_verbatim():
    """No normalisation here. The raw form IS the evidence — every key downstream
    is derived from it, and a normalisation baked in at extraction could never be
    revised without re-running the model."""
    llm = _FakeLlm('[{"surface": "  Reflow Oven (RO-3) ", "kind": "tool"}]')
    (got,) = extract_entities(llm, "…")
    assert got.surface == "Reflow Oven (RO-3)"  # only the outer whitespace goes


def test_the_kind_is_free_text_not_a_fixed_list():
    """ "機台" and "tool" are the same kind written two ways, and that is fine here
    — unifying them is the vocabulary's job, done against all the evidence, not a
    choice forced on the model one passage at a time."""
    llm = _FakeLlm('[{"surface": "RO-3", "kind": "tool"}, {"surface": "SPI", "kind": "設備"}]')
    assert [m.kind for m in extract_entities(llm, "…")] == ["tool", "設備"]


def test_an_entry_without_a_surface_is_dropped():
    """A kind with nothing to attach it to is not a mention of anything."""
    llm = _FakeLlm('[{"surface": "", "kind": "機台"}, {"kind": "製程"}, {"surface": "SPI"}]')
    assert extract_entities(llm, "…") == [EntityMention(surface="SPI", kind="")]


def test_a_repeated_surface_is_returned_twice():
    """Counting occurrences is the writer's job — it aggregates across the whole
    document, so the extractor must not silently collapse them here and lose the
    signal."""
    llm = _FakeLlm('[{"surface": "RO-3", "kind": "機台"}, {"surface": "RO-3", "kind": "tool"}]')
    assert len(extract_entities(llm, "…")) == 2


def test_a_reply_with_no_json_array_yields_nothing():
    """Never raises: one unparseable passage must not fail the batch it rides in."""
    assert extract_entities(_FakeLlm("I could not find any entities."), "…") == []


def test_a_malformed_json_array_yields_nothing():
    assert extract_entities(_FakeLlm('[{"surface": "RO-3",]'), "…") == []


def test_a_json_object_instead_of_an_array_yields_nothing():
    assert extract_entities(_FakeLlm('{"surface": "RO-3"}'), "…") == []


def test_prose_around_the_array_is_tolerated():
    """Small models wrap JSON in commentary; the batch should survive it."""
    llm = _FakeLlm('Sure! Here you go:\n[{"surface": "SPI", "kind": "機台"}]\nHope that helps.')
    assert extract_entities(llm, "…") == [EntityMention(surface="SPI", kind="機台")]
