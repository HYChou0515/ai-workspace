"""#520: the shared workflow-TEMPLATE registry — the third leg of the shipped-and-
reusable family that already has `sample-tools/` (PACKAGES) and `sample-skills/`
(SHARED_SKILLS).

A template differs from a skill in what happens to it: a skill is granted to an App and
read in place, whereas a template is COPIED into an item's `.workflows/` so the user owns
and edits their copy. So the guarantee that matters here is that every shipped template
actually parses and validates — a broken one would only be discovered by a user pressing
the button.
"""

from __future__ import annotations

import pytest

from workspace_app.workflow.dsl import DslError, validate_def
from workspace_app.workflow.shared import (
    SHARED_WORKFLOWS,
    load_shared_workflow,
    shared_workflow_metas,
)


@pytest.mark.parametrize("name", sorted(SHARED_WORKFLOWS))
def test_every_shared_template_parses_and_validates(name: str) -> None:
    """The bundled-workflow guarantee, extended to templates: a shipped template that
    doesn't validate is a button that fails only when someone presses it."""
    assert validate_def(load_shared_workflow(name)) == []


@pytest.mark.parametrize("name", sorted(SHARED_WORKFLOWS))
def test_every_shared_template_is_addressed_by_its_registry_name(name: str) -> None:
    """The registry key is the id users copy under, so a drifting `id` inside the JSON
    would silently save the copy under a different name."""
    assert load_shared_workflow(name).id == name


def test_unknown_template_name_is_rejected_with_the_available_ones() -> None:
    with pytest.raises(DslError) as exc:
        load_shared_workflow("no-such-template")
    assert "no-such-template" in str(exc.value)
    assert "image-to-knowledge" in str(exc.value)  # tells the caller what IS available


def test_metas_carry_what_a_picker_needs() -> None:
    metas = shared_workflow_metas()
    assert {m.id for m in metas} == set(SHARED_WORKFLOWS)
    for m in metas:
        assert m.title and m.description  # a nameless/undescribed card is unusable


def test_image_to_knowledge_links_its_card_to_the_document_it_files() -> None:
    """#520's whole point, asserted on the shipped artifact: the run must file the image
    as a document AND anchor a card to THAT document — not just create two unrelated
    things. Written against the template so a well-meaning edit can't quietly drop the
    link and leave a demo that no longer demonstrates anything."""
    d = load_shared_workflow("image-to-knowledge")
    steps = _flatten(d)
    ingest = next(s for s in steps if getattr(s, "call", "") == "ingest_to_collection")
    card = next(s for s in steps if getattr(s, "call", "") == "upsert_context_card")

    assert ingest.name, "the ingest step must be named or its doc id can't be referenced"
    assert card.reference_doc_ids == [f"{{steps.{ingest.name}.doc_id}}"]


def test_image_to_knowledge_reads_the_image_with_a_vision_tool() -> None:
    """A template that never looks at the image would file an empty note for every
    binary upload — the #530 failure mode. `read_image` is the load-bearing grant."""
    agents = [s for s in _flatten(load_shared_workflow("image-to-knowledge")) if _is_agent(s)]
    assert any("read_image" in s.tools for s in agents)


async def test_image_to_knowledge_runs_end_to_end_and_anchors_each_card() -> None:
    """Validation only proves the template PARSES. This drives the shipped artifact
    through the real interpreter — map, gate, both capabilities — and asserts the card
    each element commits carries the doc id THAT element's ingest produced. Per-element
    is the part worth pinning: `{steps.ingest.doc_id}` resolves against the current map
    scope, so a bug there would cross-wire two images' cards to one document, which is
    exactly the kind of thing that looks fine until someone reads the links.
    """
    import json as _json

    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.workflow.dsl import build_run
    from workspace_app.workflow.gate import record_decision
    from workspace_app.workflow.handle import WorkflowHandle

    store = MemoryFileStore()
    for name in ("a.png", "b.png"):
        await store.write("ws", f"/uploads/{name}", b"\x89PNG fake bytes")

    turns: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        turns.append(prompt)
        if "Reply with a JSON object" not in prompt:
            return "# what the image shows\ntranscribed text"
        # the note path is echoed back in the prompt; recover which element this is
        src = "notes/uploads/a.png.md" if "a.png" in prompt else "notes/uploads/b.png.md"
        return _json.dumps({"term": src, "title": "T", "summary": "S", "source": src})

    filed: list[str] = []

    async def ingest(collection: str, path: str) -> str:
        filed.append(path)
        return f"doc::{path}"

    cards: list[tuple[list[str], list[str] | None]] = []

    async def upsert(collection, keys, title, body, refs=None) -> str:
        cards.append((keys, refs))
        return "card"

    wf = WorkflowHandle(
        store=store, workspace_id="ws", workflow_id="w", user="u", config={"collection": "notes"}
    )
    wf.drive_turn = drive_turn  # type: ignore[assignment]
    wf._ingest = ingest  # type: ignore[attr-defined]
    wf._upsert_card = upsert  # type: ignore[attr-defined]

    # the template stops for a human before it writes anything to the collection; stand
    # in for that approval so the commit phase runs.
    await record_decision(wf, phase="review", choice="approve")
    assert await build_run(load_shared_workflow("image-to-knowledge"))(wf, {}) == {"status": "done"}

    assert sorted(filed) == ["notes/uploads/a.png.md", "notes/uploads/b.png.md"]
    # each card is anchored to the document ITS OWN element just filed
    assert sorted(cards) == [
        (["notes/uploads/a.png.md"], ["doc::notes/uploads/a.png.md"]),
        (["notes/uploads/b.png.md"], ["doc::notes/uploads/b.png.md"]),
    ]


def _flatten(d):  # type: ignore[no-untyped-def]
    """Every step, walking one level into a map's `do` (the DSL allows no deeper)."""
    out = []
    for s in d.steps:
        out.append(s)
        out.extend(getattr(s, "do", []) or [])
    return out


def _is_agent(step) -> bool:  # type: ignore[no-untyped-def]
    return hasattr(step, "tools")
