"""RCA Message persists `[n]` citations from `ask_knowledge_base`.

Symmetric with `KbMessage.citations`: an RCA tool message produced by the
`ask_knowledge_base` tool carries its KB sub-agent's resolved citations so
the FE can render reference cards under the tool card (same UX as direct
KB chat). Without this, the citation cards only show in KB chat — RCA
users see the answer text but no provenance.
"""

from __future__ import annotations

import msgspec

from workspace_app.resources.conversation import Citation, Message


def test_message_carries_citations_field_default_empty() -> None:
    m = Message(role="tool", content="x")
    # The field exists and defaults to an empty list so older tool messages
    # (no citations) and non-ask_knowledge_base tools stay clean.
    assert m.citations == []


def test_message_round_trips_citations_through_msgspec() -> None:
    """specstar persists Messages via msgspec.encode/decode — citations
    must survive a JSON round-trip so a reload shows the cards."""
    cite = Citation(
        marker=1,
        collection_id="col",
        document_id="doc",
        filename="reflow-spec.md",
        start=120,
        end=210,
        source_chunk_ids=["ck-a", "ck-b"],
        snippet="Zone 3 setpoint window …",
    )
    m = Message(
        role="tool",
        content="answer with [1]",
        tool_call_id="call_1",
        tool_name="ask_knowledge_base",
        citations=[cite],
    )
    raw = msgspec.json.encode(m)
    restored = msgspec.json.decode(raw, type=Message)
    assert len(restored.citations) == 1
    assert restored.citations[0].marker == 1
    assert restored.citations[0].filename == "reflow-spec.md"
