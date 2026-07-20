"""#518 DSL surface: a capability that CREATES a resource publishes its id as a
referenceable ``{steps.<name>.<field>}``, so a later step can point at what an earlier
one just made. This is what lets one run file a document and link a context card to it
(the #520 starter workflow).

The naming rule is the subtlety: for a NON-idempotent capability a ``name`` is the dedup
identity and therefore mandatory (#435 §P2). ``ingest_to_collection`` / ``upsert_context_card``
are idempotent, so a name stays OPTIONAL — it only opts the step into publishing outputs.
Shipped workflows that never named their ingest step must keep working untouched.
"""

from __future__ import annotations

import json

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import _lookup_step, build_run, parse_def, validate_def
from workspace_app.workflow.handle import WorkflowHandle


def _errs(steps: list[dict], **kw) -> list[str]:  # type: ignore[type-arg]
    d = parse_def(json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": steps}))
    return validate_def(d, **kw)


def _ingest_step(**kw) -> dict:  # type: ignore[type-arg]
    return {
        "type": "capability",
        "call": "ingest_to_collection",
        "phase": "p",
        "collection": "notes",
        "path": "uploads/a.md",
        **kw,
    }


def test_ingest_publishes_doc_id_for_a_later_step() -> None:
    """The reference #520 is built on: a named ingest exposes the document it created."""
    assert (
        _errs(
            [
                _ingest_step(name="ingest"),
                {
                    "type": "capability",
                    "call": "upsert_context_card",
                    "phase": "p",
                    "collection": "notes",
                    "keys": ["M4"],
                    "reference_doc_ids": ["{steps.ingest.doc_id}"],
                },
            ]
        )
        == []
    )


def test_ingest_does_not_require_a_name() -> None:
    """REGRESSION GUARD: ``_CAP_OUTPUTS`` membership used to double as "this capability
    must be named". Reusing it for ingest would have broken every shipped workflow whose
    ingest step is anonymous — including the bundled ``file-uploads`` example."""
    assert _errs([_ingest_step()]) == []


def test_referencing_an_unpublished_field_is_rejected() -> None:
    errs = _errs(
        [
            _ingest_step(name="ingest"),
            {"type": "agent", "prompt": "{steps.ingest.nope}", "phase": "p", "out": "o.md"},
        ]
    )
    assert any("no output field" in e for e in errs), errs


def test_referencing_an_unnamed_ingest_is_rejected() -> None:
    """Without a name there is no step to address, so the reference can't resolve."""
    errs = _errs(
        [
            _ingest_step(),
            {"type": "agent", "prompt": "{steps.ingest.doc_id}", "phase": "p", "out": "o.md"},
        ]
    )
    assert any("unknown step" in e for e in errs), errs


async def _wf() -> WorkflowHandle:
    return WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", workflow_id="w", user="u")


async def test_named_ingest_publishes_the_created_doc_id_at_run_time() -> None:
    """End-to-end through the interpreter: the id the capability returned is readable as
    ``{steps.<name>.doc_id}``."""
    wf = await _wf()
    ingested: list[tuple[str, str]] = []

    async def fake_ingest(collection: str, path: str) -> str:
        ingested.append((collection, path))
        return "doc-42"

    wf._ingest = fake_ingest  # type: ignore[attr-defined]
    d = parse_def(
        json.dumps({"id": "w", "phases": [{"id": "p"}], "steps": [_ingest_step(name="ingest")]})
    )

    assert await build_run(d)(wf, {}) == {"status": "done"}
    assert ingested == [("notes", "uploads/a.md")]
    assert await _lookup_step(["steps", "ingest", "doc_id"], {"__key__": ""}, wf) == "doc-42"


async def test_unnamed_ingest_keeps_its_legacy_receipt() -> None:
    """The compatibility half: an anonymous ingest still journals where it always did,
    under the path-derived key, so re-running a shipped workflow still skips the work it
    already did instead of re-ingesting everything."""
    wf = await _wf()

    async def fake_ingest(collection: str, path: str) -> str:
        return "doc-1"

    wf._ingest = fake_ingest  # type: ignore[attr-defined]
    d = parse_def(json.dumps({"id": "w", "phases": [{"id": "p"}], "steps": [_ingest_step()]}))

    assert await build_run(d)(wf, {}) == {"status": "done"}
    assert await wf.exists(f"{wf.journal_dir}/step_ingest/uploads_a.md.json")


async def test_named_upsert_context_card_publishes_its_card_id() -> None:
    wf = await _wf()

    async def fake_upsert(
        collection: str, keys: list[str], title: str, body: str, refs: list[str] | None
    ) -> str:
        return "card-7"

    wf._upsert_card = fake_upsert  # type: ignore[attr-defined]
    d = parse_def(
        json.dumps(
            {
                "id": "w",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "capability",
                        "call": "upsert_context_card",
                        "phase": "p",
                        "collection": "notes",
                        "keys": ["M4"],
                        "name": "card",
                    }
                ],
            }
        )
    )

    assert await build_run(d)(wf, {}) == {"status": "done"}
    assert await _lookup_step(["steps", "card", "card_id"], {"__key__": ""}, wf) == "card-7"
