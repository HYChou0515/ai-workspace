"""P2 (#435) DSL surface for the non-idempotent ``create_entity`` capability: a named
site + a per-capability ``on_duplicate`` policy set, validated statically; at run time the
capability self-dedups by its ``name`` and publishes a referenceable ``{steps.<name>.number}``.
"""

from __future__ import annotations

import json

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import _lookup_step, build_run, parse_def, validate_def
from workspace_app.workflow.handle import WorkflowHandle


def _errs(steps: list[dict], **kw) -> list[str]:  # type: ignore[type-arg]
    d = parse_def(json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": steps}))
    return validate_def(d, **kw)


def test_create_entity_capability_needs_a_name() -> None:
    """create_entity is non-idempotent: without a stable ``name`` two sites would collide,
    so a nameless one is a static error."""
    errs = _errs([{"type": "capability", "call": "create_entity", "phase": "p", "type_name": "x"}])
    assert any("needs a 'name'" in e for e in errs)


def test_create_entity_on_duplicate_policy_is_validated_per_capability() -> None:
    """``on_duplicate`` is checked against THIS capability's policy set (决议4): update/skip
    are fine for create_entity; a bogus value errors; and setting it on an idempotent
    capability that takes no policy errors."""
    ok = _errs(
        [
            {
                "type": "capability",
                "call": "create_entity",
                "phase": "p",
                "type_name": "issue",
                "name": "s",
                "on_duplicate": "skip",
            }
        ]
    )
    assert ok == []

    bogus = _errs(
        [
            {
                "type": "capability",
                "call": "create_entity",
                "phase": "p",
                "type_name": "issue",
                "name": "s",
                "on_duplicate": "nuke",
            }
        ]
    )
    assert any("'on_duplicate' must be one of" in e for e in bogus)

    on_idempotent = _errs(
        [
            {
                "type": "capability",
                "call": "ingest_to_collection",
                "phase": "p",
                "collection": "a",
                "path": "u/x",
                "on_duplicate": "update",
            }
        ]
    )
    assert any("does not take an 'on_duplicate'" in e for e in on_idempotent)


def test_reference_to_unknown_capability_output_field_is_rejected() -> None:
    """A named capability exposes its FIXED output schema, so ``{steps.<name>.<field>}``
    validates against what it actually produces — a bogus field is caught statically."""
    errs = _errs(
        [
            {
                "type": "capability",
                "call": "create_entity",
                "phase": "p",
                "type_name": "issue",
                "name": "bug",
            },
            {"type": "agent", "prompt": "n={steps.bug.nope}", "phase": "p", "out": "o.md"},
        ]
    )
    assert any("has no output field 'nope'" in e for e in errs)
    # the real field resolves cleanly
    assert (
        _errs(
            [
                {
                    "type": "capability",
                    "call": "create_entity",
                    "phase": "p",
                    "type_name": "issue",
                    "name": "bug",
                },
                {"type": "agent", "prompt": "n={steps.bug.number}", "phase": "p", "out": "o.md"},
            ]
        )
        == []
    )


async def _entity_wf() -> WorkflowHandle:
    store = MemoryFileStore()
    await store.write(
        "ws", "/.entity/issue/schema.yaml", b"path: issues\nfields:\n  title: {role: text}\n"
    )
    await store.write("ws", "/.entity/issue/skeleton.md", b"---\ntitle: {{arg.title}}\n---\n")
    return WorkflowHandle(store=store, workspace_id="ws", workflow_id="pm", user="alice")


async def test_create_entity_capability_runs_dedups_and_publishes_number() -> None:
    """End-to-end through the interpreter: a named create_entity capability mints an
    entity, publishes ``{steps.<name>.number}``, and a second run with changed content
    self-dedups (merge) instead of minting a duplicate."""
    wf = await _entity_wf()
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "capability",
                        "call": "create_entity",
                        "phase": "p",
                        "type_name": "issue",
                        "name": "file_bug",
                        "args": {"title": "{inputs.title}"},
                    }
                ],
            }
        )
    )
    assert await build_run(d)(wf, {"title": "Bug A"}) == {"status": "done"}
    assert await _lookup_step(["steps", "file_bug", "number"], {"__key__": ""}, wf) == 1

    # a revise: same site, changed content → self-dedup, no #2
    assert await build_run(d)(wf, {"title": "Bug A clarified"}) == {"status": "done"}
    store = wf._store  # type: ignore[attr-defined]
    assert not await store.exists("ws", "/issues/2.md")
    assert "Bug A clarified" in (await store.read("ws", "/issues/1.md")).decode()
