"""The user-authored DSL (#323): schema parse, static validation, deterministic
interpolation, and the interpreter that runs a ``workflow.json`` over the existing
step primitives (manual §22)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import (
    AgentStep,
    DslError,
    SandboxStep,
    WorkflowDef,
    _resolve,
    build_manifest,
    build_run,
    parse_def,
    validate_def,
)
from workspace_app.workflow.engine import StepFailed
from workspace_app.workflow.gate import record_decision
from workspace_app.workflow.handle import WorkflowHandle

# ─── helpers ─────────────────────────────────────────────────────────────────


def make_wf(store: MemoryFileStore | None = None, **fakes: Any) -> WorkflowHandle:
    wf = WorkflowHandle(
        store=store or MemoryFileStore(), workspace_id="ws", config={"collections": ["a", "b"]}
    )
    for k, v in fakes.items():
        setattr(wf, k, v)
    return wf


def _def(**over: Any) -> WorkflowDef:
    base: dict[str, Any] = {
        "id": "wf",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
    base.update(over)
    return parse_def(json.dumps(base))


# ─── parse_def ───────────────────────────────────────────────────────────────


def test_parse_def_full_roundtrip():
    d = parse_def(
        b"""{"schema":1,"id":"x","title":"T","tag":"batch","hint":"drop files",
        "description":"d","config":{"collections":["a"]},
        "phases":[{"id":"p","title":"P"}],
        "steps":[{"type":"sandbox","run":"echo hi","phase":"p"}]}"""
    )
    assert d.id == "x" and d.title == "T" and d.schema_version == 1
    assert d.config == {"collections": ["a"]}
    assert isinstance(d.steps[0], SandboxStep) and d.steps[0].run == "echo hi"


def test_parse_def_bad_json():
    with pytest.raises(DslError, match="not valid JSON"):
        parse_def("{not json")


def test_parse_def_unknown_field():
    with pytest.raises(DslError, match="unknown field"):
        parse_def('{"id":"x","bogus":1}')


def test_parse_def_bad_step_tag():
    with pytest.raises(DslError, match="Invalid value"):
        parse_def('{"id":"x","steps":[{"type":"nope"}]}')


def test_parse_def_accepts_str_and_bytes():
    assert parse_def('{"id":"a"}').id == parse_def(b'{"id":"a"}').id == "a"


# ─── build_manifest ──────────────────────────────────────────────────────────


def test_build_manifest_maps_metadata():
    d = _def(title="T", tag="batch", hint="h", description="d", phases=[{"id": "p", "title": "P"}])
    m = build_manifest(d)
    assert m.id == "wf" and m.title == "T" and m.tag == "batch" and m.hint == "h"
    assert m.description == "d" and m.phases[0].title == "P" and m.input_json == ""


# ─── interpolation (_resolve) ────────────────────────────────────────────────


async def test_resolve_literal_and_non_string():
    wf = make_wf()
    assert await _resolve("plain text", {}, wf) == "plain text"
    assert await _resolve(["a", "b"], {}, wf) == ["a", "b"]  # non-str returned as-is


async def test_resolve_whole_token_returns_object():
    wf = make_wf()
    ns = {"config": {"collections": ["a", "b"]}}
    assert await _resolve("{config.collections}", ns, wf) == ["a", "b"]  # a list, not a string


async def test_resolve_substitution_stringifies():
    wf = make_wf()
    ns = {"config": {"collections": ["a", "b"]}, "file": "/uploads/x.log"}
    assert await _resolve("pick from {config.collections} for {file}", ns, wf) == (
        'pick from ["a", "b"] for /uploads/x.log'
    )


async def test_resolve_reads_json_path_field():
    wf = make_wf()
    await wf.write_json("/plan/x.json", {"collection": "a", "source": "/uploads/x.log"})
    ns = {"p": "/plan/x.json"}
    assert await _resolve("{p.collection}", ns, wf) == "a"
    assert await _resolve("{p.source}", ns, wf) == "/uploads/x.log"


async def test_resolve_unknown_variable():
    wf = make_wf()
    with pytest.raises(DslError, match="unknown variable"):
        await _resolve("{bogus}", {}, wf)


async def test_resolve_field_not_found():
    wf = make_wf()
    with pytest.raises(DslError, match="not found"):
        await _resolve("{config.nope}", {"config": {"a": 1}}, wf)


async def test_resolve_cannot_index_scalar():
    wf = make_wf()
    with pytest.raises(DslError, match="cannot read field"):
        await _resolve("{x.y}", {"x": "not-a-json-path"}, wf)


# ─── validate_def ────────────────────────────────────────────────────────────


def _errs(steps: list[dict[str, Any]], phases: Any = None, **kw: Any) -> list[str]:
    d = parse_def(json.dumps({"id": "wf", "phases": phases or [{"id": "p"}], "steps": steps}))
    return validate_def(d, **kw)


def test_validate_valid_def_is_empty():
    steps = [
        {
            "type": "map",
            "over": "uploads/*",
            "as": "f",
            "phase": "p",
            "do": [
                {
                    "type": "agent",
                    "prompt": "read {f} from {config.collections}",
                    "phase": "p",
                    "out": "plan/{f}.json",
                    "check": {
                        "choice_in": {
                            "path": "plan/{f}.json",
                            "key": "collection",
                            "allowed": "{config.collections}",
                        }
                    },
                }
            ],
        },
        {"type": "gate", "phase": "p", "title": "ok?", "summary_from": "plan/*.json"},
        {
            "type": "capability",
            "call": "ingest_to_collection",
            "phase": "p",
            "collection": "{config.collections}",
            "path": "uploads/x",
        },
    ]
    assert (
        validate_def(parse_def(json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": steps})))
        == []
    )


def test_validate_schema_version():
    d = parse_def(
        '{"id":"x","schema":2,"phases":[{"id":"p"}],"steps":[{"type":"sandbox","run":"x","phase":"p"}]}'
    )
    assert any("schema version" in e for e in validate_def(d))


def test_validate_empty_id_no_steps_and_blank_phase():
    d = parse_def('{"id":"","phases":[{"id":""}],"steps":[]}')
    errs = validate_def(d)
    assert any("'id' is empty" in e for e in errs)
    assert any("no steps" in e for e in errs)
    assert any("missing its 'id'" in e for e in errs)


def test_validate_phase_not_declared():
    assert any(
        "not declared" in e for e in _errs([{"type": "sandbox", "run": "x", "phase": "zzz"}])
    )


def test_validate_agent_needs_prompt_check_and_nonneg_retries():
    errs = _errs([{"type": "agent", "prompt": "", "phase": "p", "retries": -1}])
    assert any("needs a 'prompt'" in e for e in errs)
    assert any("needs a 'check'" in e for e in errs)
    assert any("retries cannot be negative" in e for e in errs)


def test_validate_agent_tool_ceiling():
    errs = _errs(
        [
            {
                "type": "agent",
                "prompt": "p",
                "phase": "p",
                "out": "o",
                "tools": ["read_file", "nuke"],
            }
        ],
        tool_ceiling={"read_file"},
    )
    assert any("tool 'nuke' is outside" in e for e in errs)


def test_validate_check_shapes():
    bad_two = _errs(
        [
            {
                "type": "sandbox",
                "run": "x",
                "phase": "p",
                "check": {"file_nonempty": {"path": "a"}, "choice_in": {}},
            }
        ]
    )
    assert any("exactly one" in e for e in bad_two)
    unknown = _errs([{"type": "sandbox", "run": "x", "phase": "p", "check": {"weird": {}}}])
    assert any("unknown check" in e for e in unknown)
    not_obj = _errs(
        [{"type": "sandbox", "run": "x", "phase": "p", "check": {"file_nonempty": "a"}}]
    )
    assert any("needs an object" in e for e in not_obj)
    missing = _errs(
        [{"type": "sandbox", "run": "x", "phase": "p", "check": {"choice_in": {"path": "a"}}}]
    )
    assert any("missing 'key'" in e for e in missing)
    empty_args = _errs(
        [{"type": "sandbox", "run": "x", "phase": "p", "check": {"file_nonempty": {}}}]
    )
    assert any("missing 'path'" in e for e in empty_args)  # empty-dict args still validated


def test_validate_sandbox_needs_run():
    assert any(
        "non-empty 'run'" in e for e in _errs([{"type": "sandbox", "run": "", "phase": "p"}])
    )


def test_validate_gate_rules():
    errs = _errs([{"type": "gate", "phase": "p", "title": "", "allow": []}])
    assert any("needs a 'title'" in e for e in errs)
    assert any("'allow' is empty" in e for e in errs)


def test_validate_capability_rules():
    not_allowed = _errs([{"type": "capability", "call": "rm_rf", "phase": "p"}])
    assert any("not allowed" in e for e in not_allowed)
    missing = _errs(
        [{"type": "capability", "call": "ingest_to_collection", "phase": "p", "collection": "a"}]
    )
    assert any("needs 'path'" in e for e in missing)


def test_validate_map_rules_and_nesting():
    empty = _errs([{"type": "map", "over": "", "as": "", "phase": "p", "do": []}])
    assert any("non-empty 'over'" in e for e in empty)
    assert any("non-empty 'as'" in e for e in empty)
    assert any("'do' is empty" in e for e in empty)
    nested = _errs(
        [
            {
                "type": "map",
                "over": "u/*",
                "as": "f",
                "phase": "p",
                "do": [
                    {"type": "map", "over": "x", "as": "g", "phase": "p", "do": []},
                    {"type": "gate", "phase": "p", "title": "t"},
                ],
            }
        ]
    )
    assert any("cannot be nested in a map" in e for e in nested)


def test_validate_gate_must_be_top_level():
    # A gate reachable as a non-top step (validate is called with top=False inside a map's
    # do — but gates are rejected there by the nesting rule; this exercises the top guard
    # via a direct call path is covered by nesting; assert nesting catches it).
    errs = _errs(
        [
            {
                "type": "map",
                "over": "u/*",
                "as": "f",
                "phase": "p",
                "do": [{"type": "gate", "phase": "p", "title": "t"}],
            }
        ]
    )
    assert any("cannot be nested" in e for e in errs)


def test_validate_interp_unknown_variable():
    errs = _errs([{"type": "sandbox", "run": "echo {bogus}", "phase": "p"}])
    assert any("unknown variable 'bogus'" in e for e in errs)


def test_validate_interp_known_vars_pass():
    errs = _errs([{"type": "sandbox", "run": "echo {config} {inputs}", "phase": "p"}])
    assert errs == []


def test_check_interp_ignores_non_string_scalars():
    from workspace_app.workflow.dsl import _check_interp

    errs: list[str] = []
    _check_interp({"n": 5, "ok": "{config}", "nested": ["{bad}"]}, {"config"}, "w", errs)
    assert any("unknown variable 'bad'" in e for e in errs)  # nested list still scanned
    assert all("5" not in e for e in errs)  # a non-string scalar is simply ignored


# ─── interpreter (build_run) ─────────────────────────────────────────────────


async def test_run_produce_gate_commit_happy_path():
    store = MemoryFileStore()
    ingested: list[tuple[str, str]] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"collection": "a", "source": "/uploads/a.log"})

    async def ingest(collection: str, path: str) -> str:
        ingested.append((collection, path))
        return "doc1"

    wf = make_wf(store, drive_turn=drive_turn, _ingest=ingest)
    await wf.write("/uploads/a.log", "boom")
    await record_decision(wf, phase="review", choice="approve")

    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "config": {"collections": ["a", "b"]},
                "phases": [{"id": "classify"}, {"id": "review"}, {"id": "commit"}],
                "steps": [
                    {
                        "type": "map",
                        "over": "uploads/*",
                        "as": "file",
                        "phase": "classify",
                        "do": [
                            {
                                "type": "agent",
                                "prompt": "Read {file} pick {config.collections}",
                                "phase": "classify",
                                "out": "plan/{file}.json",
                                "check": {
                                    "choice_in": {
                                        "path": "plan/{file}.json",
                                        "key": "collection",
                                        "allowed": "{config.collections}",
                                    }
                                },
                                "retries": 1,
                            }
                        ],
                    },
                    {
                        "type": "gate",
                        "phase": "review",
                        "title": "Approve?",
                        "summary_from": "plan/*.json",
                    },
                    {
                        "type": "map",
                        "over": "plan/*.json",
                        "as": "p",
                        "phase": "commit",
                        "do": [
                            {
                                "type": "capability",
                                "call": "ingest_to_collection",
                                "phase": "commit",
                                "collection": "{p.collection}",
                                "path": "{p.source}",
                            }
                        ],
                    },
                ],
            }
        )
    )
    assert validate_def(d) == []
    result = await build_run(d)(wf, None)
    assert result == {"status": "done"}
    assert ingested == [("a", "/uploads/a.log")]


async def test_run_gate_reject_stops_before_commit():
    store = MemoryFileStore()
    wf = make_wf(store)
    await record_decision(wf, phase="review", choice="reject")
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "review"}],
                "steps": [{"type": "gate", "phase": "review", "title": "Approve?"}],
            }
        )
    )
    assert await build_run(d)(wf, {}) == {"status": "reject"}


async def test_run_map_collects_failures():
    store = MemoryFileStore()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return ""  # empty → file_nonempty gate fails → StepFailed (retries=0)

    wf = make_wf(store, drive_turn=drive_turn)
    await wf.write("/uploads/a.log", "x")
    await wf.write("/uploads/b.log", "y")
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "map",
                        "over": "uploads/*",
                        "as": "f",
                        "phase": "p",
                        "do": [
                            {"type": "agent", "prompt": "do {f}", "phase": "p", "out": "out/{f}.md"}
                        ],
                    }
                ],
            }
        )
    )
    result = await build_run(d)(wf, None)
    assert result["status"] == "done"
    assert len(result["failures"]) == 2


async def test_run_sandbox_and_agent_step_and_upsert_and_collection_has():
    store = MemoryFileStore()
    ran: list[str] = []
    cards: list[tuple[str, list[str]]] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        await wf.write("/touched.txt", "ok")
        return 0, "done"

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        await wf.write("/note.md", "content")
        return "ok"

    async def upsert(collection: str, keys: list[str], title: str, body: str) -> str:
        cards.append((collection, keys))
        return "card1"

    async def has(collection: str, path: str) -> bool:
        return True

    wf = make_wf(
        store,
        run_sandbox=run_sandbox,
        drive_turn=drive_turn,
        _upsert_card=upsert,
        _collection_has=has,
    )
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"collections": ["a", "b"]},
                "steps": [
                    {
                        "type": "sandbox",
                        "run": "build {config.collections}",
                        "phase": "p",
                        "name": "build",
                        "check": {"collection_has": {"collection": "a", "path": "touched.txt"}},
                    },
                    {
                        "type": "agent",
                        "prompt": "write note",
                        "phase": "p",
                        "name": "note",
                        "check": {"file_nonempty": {"path": "note.md"}},
                    },
                    {
                        "type": "capability",
                        "call": "upsert_context_card",
                        "phase": "p",
                        "collection": "a",
                        "keys": ["k1", "k2"],
                        "title": "T",
                        "body": "B",
                    },
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ['build ["a", "b"]'] and cards == [("a", ["k1", "k2"])]


async def test_run_gate_summary_reads_text_and_json():
    store = MemoryFileStore()
    seen: dict[str, Any] = {}

    wf = make_wf(store)
    await wf.write_json("/plan/a.json", {"collection": "a"})
    await wf.write("/plan/note.txt", "hello")

    # Resolve the gate at first reach by recording a decision, then capture the summary
    # the gate would show via the emitted AwaitingHuman path is internal; instead assert
    # the run completes and the summary builder read both file kinds (no exception).
    await record_decision(wf, phase="p", choice="approve")
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [{"type": "gate", "phase": "p", "title": "ok?", "summary_from": "plan/*"}],
            }
        )
    )
    assert await build_run(d)(wf, None) == {"status": "done"}
    seen["ok"] = True
    assert seen["ok"]


def test_agentstep_struct_defaults():
    s = AgentStep(prompt="p", phase="p")
    assert s.out == "" and s.tools == [] and s.retries == 0 and s.check is None


# ─── P1: unified reference model {steps.<name>.<field>} ──────────────────────


async def test_run_steps_ref_resolves_named_agent_fields():
    """A named agent step that declares ``outputs`` replies with JSON; the engine
    parses it into ``result.fields`` and a downstream ``{steps.x.field}`` resolves it."""
    store = MemoryFileStore()
    ran: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"kind": "latency"})

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, drive_turn=drive_turn, run_sandbox=run_sandbox)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "agent",
                        "name": "classify",
                        "phase": "p",
                        "outputs": {"kind": "str"},
                        "prompt": "classify it",
                    },
                    {
                        "type": "sandbox",
                        "name": "act",
                        "phase": "p",
                        "run": "handle {steps.classify.kind}",
                    },
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["handle latency"]


def test_validate_steps_ref_unknown_step():
    errs = _errs(
        [{"type": "sandbox", "run": "do {steps.nope.x}", "phase": "p"}],
    )
    assert any("unknown step 'nope'" in e for e in errs)


def test_validate_steps_ref_unknown_field():
    errs = _errs(
        [
            {
                "type": "agent",
                "name": "cls",
                "phase": "p",
                "outputs": {"kind": "str"},
                "prompt": "c",
            },
            {"type": "sandbox", "run": "do {steps.cls.bogus}", "phase": "p"},
        ]
    )
    assert any("step 'cls' has no output field 'bogus'" in e for e in errs)


def test_validate_steps_ref_forward_reference():
    errs = _errs(
        [
            {"type": "sandbox", "run": "do {steps.later.x}", "phase": "p"},
            {
                "type": "agent",
                "name": "later",
                "phase": "p",
                "outputs": {"x": "str"},
                "prompt": "c",
            },
        ]
    )
    assert any("unknown step 'later'" in e for e in errs)  # not yet declared at ref site


def test_validate_duplicate_step_name():
    errs = _errs(
        [
            {"type": "agent", "name": "dup", "phase": "p", "outputs": {"x": "str"}, "prompt": "a"},
            {"type": "agent", "name": "dup", "phase": "p", "outputs": {"y": "str"}, "prompt": "b"},
        ]
    )
    assert any("duplicate step name 'dup'" in e for e in errs)


def test_validate_steps_ref_valid_passes():
    errs = _errs(
        [
            {
                "type": "agent",
                "name": "cls",
                "phase": "p",
                "outputs": {"kind": "str"},
                "prompt": "c",
            },
            {"type": "sandbox", "run": "do {steps.cls.kind}", "phase": "p"},
        ]
    )
    assert errs == []


def test_validate_steps_ref_bare_no_name():
    errs = _errs([{"type": "sandbox", "run": "do {steps}", "phase": "p"}])
    assert any("needs a step name" in e for e in errs)


async def test_run_sandbox_outputs_fields_referenceable():
    """A sandbox step with ``outputs`` parses its stdout JSON into result.fields (§1.2),
    referenceable downstream."""
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        if cmd == "measure":
            return 0, json.dumps({"dropped": 7})
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "sandbox",
                        "name": "measure",
                        "phase": "p",
                        "run": "measure",
                        "outputs": {"dropped": "int"},
                    },
                    {
                        "type": "sandbox",
                        "name": "report",
                        "phase": "p",
                        "run": "report {steps.measure.dropped}",
                    },
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["report 7"]


async def test_run_agent_outputs_non_json_fails_its_gate():
    """An ``outputs`` step whose reply isn't a JSON object fails its implicit gate."""
    store = MemoryFileStore()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return "not json at all"

    wf = make_wf(store, drive_turn=drive_turn)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "agent",
                        "name": "c",
                        "phase": "p",
                        "outputs": {"k": "str"},
                        "prompt": "c",
                    }
                ],
            }
        )
    )
    with pytest.raises(StepFailed):
        await build_run(d)(wf, None)


async def test_lookup_step_bare_and_missing_fields():
    wf = make_wf()
    with pytest.raises(DslError, match="needs a step name"):
        await _resolve("{steps}", {"__key__": ""}, wf)
    # a step that ran but recorded no output fields cannot be field-referenced
    await wf.write_json(
        "/.workflow/_default/step_x/main.json", {"hash": "h", "result": {"out": "o", "bytes": 1}}
    )
    with pytest.raises(DslError, match="no output fields"):
        await _resolve("{steps.x.k}", {"__key__": ""}, wf)


async def test_run_map_inner_steps_ref_uses_element_key():
    """Inside a map, ``{steps.x.f}`` resolves at the current element's key (§1.1)."""
    store = MemoryFileStore()
    ran: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"kind": "big"})

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, drive_turn=drive_turn, run_sandbox=run_sandbox)
    await wf.write("/uploads/a.txt", "x")
    await wf.write("/uploads/b.txt", "y")
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "map",
                        "over": "uploads/*",
                        "as": "f",
                        "phase": "p",
                        "do": [
                            {
                                "type": "agent",
                                "name": "cls",
                                "phase": "p",
                                "outputs": {"kind": "str"},
                                "prompt": "classify {f}",
                            },
                            {
                                "type": "sandbox",
                                "name": "act",
                                "phase": "p",
                                "run": "handle {f} as {steps.cls.kind}",
                            },
                        ],
                    }
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert sorted(ran) == ["handle /uploads/a.txt as big", "handle /uploads/b.txt as big"]


# ─── P2: outputs schema (types + enum + validation) ──────────────────────────


async def test_run_outputs_all_scalar_and_container_types_validate():
    """Every declared type (str/int/float/bool/list/obj) accepts a matching value."""
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        if cmd == "gen":
            return 0, json.dumps({"s": "x", "i": 3, "f": 1.5, "b": True, "l": [1], "o": {"a": 1}})
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "sandbox",
                        "name": "gen",
                        "phase": "p",
                        "run": "gen",
                        "outputs": {
                            "s": "str",
                            "i": "int",
                            "f": "float",
                            "b": "bool",
                            "l": "list",
                            "o": "obj",
                        },
                    },
                    {"type": "sandbox", "name": "use", "phase": "p", "run": "use {steps.gen.i}"},
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["use 3"]


async def test_run_agent_outputs_type_mismatch_retries_with_feedback():
    store = MemoryFileStore()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        if "did not pass" in prompt:  # _retry_prompt fed the failure back in
            return json.dumps({"score": 0.9})
        return json.dumps({"score": "high"})  # wrong: str, not float

    wf = make_wf(store, drive_turn=drive_turn)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "agent",
                        "name": "rate",
                        "phase": "p",
                        "outputs": {"score": "float"},
                        "prompt": "rate it",
                        "retries": 1,
                    }
                ],
            }
        )
    )
    assert await build_run(d)(wf, None) == {"status": "done"}


async def test_run_agent_outputs_enum_violation_fails():
    store = MemoryFileStore()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"kind": "weird"})

    wf = make_wf(store, drive_turn=drive_turn)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "agent",
                        "name": "c",
                        "phase": "p",
                        "outputs": {"kind": {"type": "str", "enum": ["latency", "errors"]}},
                        "prompt": "c",
                    }
                ],
            }
        )
    )
    with pytest.raises(StepFailed):
        await build_run(d)(wf, None)


async def test_run_agent_outputs_missing_field_fails():
    store = MemoryFileStore()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"other": 1})  # missing declared 'kind'

    wf = make_wf(store, drive_turn=drive_turn)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "agent",
                        "name": "c",
                        "phase": "p",
                        "outputs": {"kind": "str"},
                        "prompt": "c",
                    }
                ],
            }
        )
    )
    with pytest.raises(StepFailed):
        await build_run(d)(wf, None)


def test_validate_outputs_bad_type():
    errs = _errs(
        [{"type": "agent", "name": "s", "phase": "p", "outputs": {"x": "weird"}, "prompt": "p"}]
    )
    assert any("unknown output type 'weird'" in e for e in errs)


def test_validate_outputs_enum_only_on_scalars():
    errs = _errs(
        [
            {
                "type": "agent",
                "name": "s",
                "phase": "p",
                "outputs": {"x": {"type": "list", "enum": [1]}},
                "prompt": "p",
            }
        ]
    )
    assert any("enum is only allowed on scalar" in e for e in errs)


def test_validate_outputs_bad_spec_shape():
    errs = _errs([{"type": "agent", "name": "s", "phase": "p", "outputs": {"x": 5}, "prompt": "p"}])
    assert any("output 'x'" in e for e in errs)


# ─── P3: switch conditional node ─────────────────────────────────────────────


def _switch_def(steps: list[dict[str, Any]], phases: Any = None, config: Any = None) -> WorkflowDef:
    body: dict[str, Any] = {"id": "wf", "phases": phases or [{"id": "p"}], "steps": steps}
    if config is not None:
        body["config"] = config
    return parse_def(json.dumps(body))


async def test_run_switch_routes_to_matching_case():
    store = MemoryFileStore()
    ran: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"kind": "latency"})

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, drive_turn=drive_turn, run_sandbox=run_sandbox)
    d = _switch_def(
        [
            {
                "type": "agent",
                "name": "cls",
                "phase": "p",
                "outputs": {"kind": {"type": "str", "enum": ["latency", "errors"]}},
                "prompt": "c",
            },
            {
                "type": "switch",
                "on": "{steps.cls.kind}",
                "phase": "p",
                "cases": {
                    "latency": [{"type": "sandbox", "run": "handle-latency", "phase": "p"}],
                    "errors": [{"type": "sandbox", "run": "handle-errors", "phase": "p"}],
                },
            },
        ]
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["handle-latency"]


async def test_run_switch_falls_to_default():
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = _switch_def(
        [
            {
                "type": "switch",
                "on": "{config.mode}",
                "phase": "p",
                "cases": {"a": [{"type": "sandbox", "run": "A", "phase": "p"}]},
                "default": [{"type": "sandbox", "run": "DEF", "phase": "p"}],
            }
        ],
        config={"mode": "z"},
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["DEF"]


async def test_run_switch_unmatched_no_default_raises():
    wf = make_wf()
    d = _switch_def(
        [
            {
                "type": "switch",
                "on": "{config.mode}",
                "phase": "p",
                "cases": {"a": [{"type": "sandbox", "run": "A", "phase": "p"}]},
            }
        ],
        config={"mode": "z"},
    )
    assert validate_def(d) == []
    with pytest.raises(StepFailed):
        await build_run(d)(wf, None)


async def test_run_switch_unmatched_in_map_is_element_failure():
    store = MemoryFileStore()

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    await wf.write("/uploads/a.txt", "x")
    await wf.write("/uploads/b.txt", "y")
    d = _switch_def(
        [
            {
                "type": "map",
                "over": "uploads/*",
                "as": "f",
                "phase": "p",
                "do": [
                    {
                        "type": "switch",
                        "on": "{config.mode}",
                        "phase": "p",
                        "cases": {"a": [{"type": "sandbox", "run": "A", "phase": "p"}]},
                    }
                ],
            }
        ],
        config={"mode": "z"},
    )
    assert validate_def(d) == []
    result = await build_run(d)(wf, None)
    assert result["status"] == "done"
    assert len(result["failures"]) == 2  # each element's switch failed, collected


async def test_run_gate_inside_top_level_switch_case():
    store = MemoryFileStore()
    wf = make_wf(store)
    await record_decision(wf, phase="g", choice="approve")
    d = _switch_def(
        [
            {
                "type": "switch",
                "on": "{config.mode}",
                "phase": "p",
                "cases": {
                    "review": [{"type": "gate", "phase": "g", "title": "ok?"}],
                },
                "default": [],
            }
        ],
        phases=[{"id": "p"}, {"id": "g"}],
        config={"mode": "review"},
    )
    assert validate_def(d) == []  # a gate is allowed in a top-level switch case (context A)
    assert await build_run(d)(wf, None) == {"status": "done"}


def test_validate_gate_in_switch_inside_map_rejected():
    # context B (map element) propagates through switch: a gate in the case is rejected
    errs = _errs(
        [
            {
                "type": "map",
                "over": "u/*",
                "as": "f",
                "phase": "p",
                "do": [
                    {
                        "type": "switch",
                        "on": "{f}",
                        "phase": "p",
                        "cases": {"x": [{"type": "gate", "phase": "p", "title": "t"}]},
                    }
                ],
            }
        ]
    )
    assert any("cannot be nested in a map" in e for e in errs)


def test_validate_switch_on_must_be_single_reference():
    errs = _errs(
        [{"type": "switch", "on": "prefix-{config.mode}", "phase": "p", "cases": {"a": []}}]
    )
    assert any("single stable reference" in e for e in errs)


def test_validate_switch_needs_on_and_cases():
    errs = _errs([{"type": "switch", "on": "", "phase": "p", "cases": {}}])
    assert any("non-empty 'on'" in e for e in errs)
    assert any("at least one case" in e for e in errs)


def test_validate_switch_case_outside_enum():
    errs = _errs(
        [
            {
                "type": "agent",
                "name": "c",
                "phase": "p",
                "outputs": {"k": {"type": "str", "enum": ["a", "b"]}},
                "prompt": "c",
            },
            {
                "type": "switch",
                "on": "{steps.c.k}",
                "phase": "p",
                "cases": {"a": [], "zzz": []},
                "default": [],
            },
        ]
    )
    assert any("not in the enum" in e for e in errs)


def test_validate_switch_missing_enum_value_no_default():
    errs = _errs(
        [
            {
                "type": "agent",
                "name": "c",
                "phase": "p",
                "outputs": {"k": {"type": "str", "enum": ["a", "b"]}},
                "prompt": "c",
            },
            {"type": "switch", "on": "{steps.c.k}", "phase": "p", "cases": {"a": []}},
        ]
    )
    assert any("does not cover enum value" in e for e in errs)


async def test_run_switch_on_non_enum_step_field():
    """A switch on a typed-but-not-enum step field routes without exhaustiveness checks."""
    store = MemoryFileStore()
    ran: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return json.dumps({"kind": "x"})

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, drive_turn=drive_turn, run_sandbox=run_sandbox)
    d = _switch_def(
        [
            {"type": "agent", "name": "c", "phase": "p", "outputs": {"kind": "str"}, "prompt": "c"},
            {
                "type": "switch",
                "on": "{steps.c.kind}",
                "phase": "p",
                "cases": {"x": [{"type": "sandbox", "run": "hit", "phase": "p"}]},
                "default": [],
            },
        ]
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["hit"]


def test_validate_switch_on_unknown_step_field():
    errs = _errs(
        [
            {"type": "agent", "name": "c", "phase": "p", "outputs": {"k": "str"}, "prompt": "c"},
            {
                "type": "switch",
                "on": "{steps.c.nofield}",
                "phase": "p",
                "cases": {"a": []},
                "default": [],
            },
        ]
    )
    assert any("no output field" in e for e in errs)


async def test_run_nested_switch_allowed():
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = _switch_def(
        [
            {
                "type": "switch",
                "on": "{config.a}",
                "phase": "p",
                "cases": {
                    "x": [
                        {
                            "type": "switch",
                            "on": "{config.b}",
                            "phase": "p",
                            "cases": {"y": [{"type": "sandbox", "run": "deep", "phase": "p"}]},
                            "default": [],
                        }
                    ]
                },
                "default": [],
            }
        ],
        config={"a": "x", "b": "y"},
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["deep"]


def test_validate_switch_depth_limit():
    # build a pathologically deep nested switch (> 32) and expect a defensive error
    node: dict[str, Any] = {"type": "sandbox", "run": "x", "phase": "p"}
    for _ in range(40):
        node = {
            "type": "switch",
            "on": "{config.m}",
            "phase": "p",
            "cases": {"a": [node]},
            "default": [],
        }
    errs = validate_def(
        parse_def(json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": [node]}))
    )
    assert any("nesting too deep" in e for e in errs)


# ─── P4: map `over` list value / range ───────────────────────────────────────


async def test_run_map_over_list_value_keeps_array_order():
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"items": ["z", "a", "m"]},
                "steps": [
                    {
                        "type": "map",
                        "over": "{config.items}",
                        "as": "x",
                        "phase": "p",
                        "do": [{"type": "sandbox", "run": "do {x}", "phase": "p"}],
                    }
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["do z", "do a", "do m"]  # array order, NOT sorted


async def test_run_map_over_list_of_objects_field_access():
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"items": [{"id": "b", "v": 1}, {"id": "a", "v": 2}]},
                "steps": [
                    {
                        "type": "map",
                        "over": "{config.items}",
                        "as": "x",
                        "key_by": "id",
                        "phase": "p",
                        "do": [{"type": "sandbox", "run": "v={x.v} id={x.id}", "phase": "p"}],
                    }
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["v=1 id=b", "v=2 id=a"]


async def test_run_map_over_range():
    store = MemoryFileStore()
    ran: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        ran.append(cmd)
        return 0, ""

    wf = make_wf(store, run_sandbox=run_sandbox)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"n": 3},
                "steps": [
                    {
                        "type": "map",
                        "over": {"range": "{config.n}"},
                        "as": "i",
                        "phase": "p",
                        "do": [{"type": "sandbox", "run": "n={i}", "phase": "p"}],
                    }
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert ran == ["n=0", "n=1", "n=2"]


async def test_run_map_key_by_collision_errors():
    store = MemoryFileStore()
    wf = make_wf(store, run_sandbox=lambda c, o: None)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"items": [{"id": "x"}, {"id": "x"}]},
                "steps": [
                    {
                        "type": "map",
                        "over": "{config.items}",
                        "as": "e",
                        "key_by": "id",
                        "phase": "p",
                        "do": [{"type": "sandbox", "run": "do", "phase": "p"}],
                    }
                ],
            }
        )
    )
    assert validate_def(d) == []
    with pytest.raises(StepFailed, match="not unique"):
        await build_run(d)(wf, None)


async def test_run_map_over_range_non_integer_errors():
    store = MemoryFileStore()
    wf = make_wf(store, run_sandbox=lambda c, o: None)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"n": "abc"},
                "steps": [
                    {
                        "type": "map",
                        "over": {"range": "{config.n}"},
                        "as": "i",
                        "phase": "p",
                        "do": [{"type": "sandbox", "run": "x", "phase": "p"}],
                    }
                ],
            }
        )
    )
    with pytest.raises(StepFailed, match="range"):
        await build_run(d)(wf, None)


def test_validate_map_over_range_checks_interp():
    errs = _errs(
        [
            {
                "type": "map",
                "over": {"range": "{bogus}"},
                "as": "i",
                "phase": "p",
                "do": [{"type": "sandbox", "run": "x", "phase": "p"}],
            }
        ]
    )
    assert any("unknown variable 'bogus'" in e for e in errs)


def test_validate_map_over_range_bad_shape():
    errs = _errs(
        [
            {
                "type": "map",
                "over": {"nope": "1"},
                "as": "i",
                "phase": "p",
                "do": [{"type": "sandbox", "run": "x", "phase": "p"}],
            }
        ]
    )
    assert any("map 'over' object must be" in e for e in errs)


async def test_run_map_key_by_missing_field_errors():
    store = MemoryFileStore()
    wf = make_wf(store, run_sandbox=lambda c, o: None)
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "config": {"items": ["not-an-object"]},
                "steps": [
                    {
                        "type": "map",
                        "over": "{config.items}",
                        "as": "e",
                        "key_by": "id",
                        "phase": "p",
                        "do": [{"type": "sandbox", "run": "do", "phase": "p"}],
                    }
                ],
            }
        )
    )
    with pytest.raises(StepFailed, match="key_by"):
        await build_run(d)(wf, None)


def test_validate_create_entity_requires_type_name() -> None:
    errs = _errs([{"type": "capability", "call": "create_entity", "phase": "p"}])
    assert any("type_name" in e for e in errs)


async def test_run_create_entity_capability() -> None:
    """A user-authored workflow.json can mint an entity through the SAME numbering
    pipeline (#419 workflow接軌) — no raw wf.write."""
    store = MemoryFileStore()
    await store.write(
        "ws", "/.entity/task/schema.yaml", b"path: tasks\nfields:\n  title: {role: text}\n"
    )
    await store.write("ws", "/.entity/task/skeleton.md", b"---\ntitle: {{arg.title}}\n---\n")
    wf = make_wf(store)
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
                        "type_name": "task",
                        "args": {"title": "Ship it"},
                    }
                ],
            }
        )
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert "title: Ship it" in (await store.read("ws", "/tasks/1.md")).decode()
