"""User-authored workflows as declarative data — the "downgraded DSL" (#323).

A ``workflow.json`` is a **non-Turing-complete** description of an orchestration that
a *trusted* interpreter runs over the SAME primitives a hand-written ``run.py`` uses
(``agent_step`` / ``sandbox_node`` / ``human_gate`` / ``wf.ingest_to_collection`` /
``wf.map``, manual §5). The JSON is **data**, the interpreter is trusted — so no user
*code* runs in the API (manual §1, §22). It's how a non-engineer co-authors a runnable
workflow with the AI (like a skill, #298): they write data, not Python.

The §9 filesystem-journal + input-hash skip works unchanged: the DSL is fixed and
interpolation is deterministic, so each step's identity (``name``/``key``) is stable
across re-runs. ``build_run`` turns a parsed ``WorkflowDef`` into a ``ProfileRun`` the
existing orchestrator runs as-is; the same interpreter serves a *package* ``workflow.json``
(a promote target — manual §22, Q6) and a *workspace* one.

Grill decisions (Q1–Q9) and the schema walk-through live in ``docs/plan-issue-323.md``;
the spec is ``docs/workflows.md`` §22.
"""

from __future__ import annotations

import json
import re
from typing import Any

import msgspec
from msgspec import Struct, field

from .checks import choice_in, collection_has, file_nonempty
from .engine import Check, CheckResult, StepFailed, _artifact_path
from .gate import human_gate
from .handle import WorkflowHandle
from .manifest import WorkflowManifest, WorkflowPhase
from .steps import agent_step, agent_write_step, sandbox_node

# The capability calls a user DSL may invoke (manual §22, Q4). Each maps to a
# ``WorkflowHandle`` method that runs under the captured user's authz; a ``sandbox``
# step gets no credential, so reliable side-effects only ever go through these.
CAPABILITIES = ("ingest_to_collection", "upsert_context_card", "create_entity")
_CAP_REQUIRED: dict[str, tuple[str, ...]] = {
    "ingest_to_collection": ("collection", "path"),
    "upsert_context_card": ("collection", "keys"),
    "create_entity": ("type_name",),
}
# The deterministic gate builders a check spec may name (manual §6).
_CHECKS = ("file_nonempty", "choice_in", "collection_has")
_CHECK_REQUIRED: dict[str, tuple[str, ...]] = {
    "file_nonempty": ("path",),
    "choice_in": ("path", "key", "allowed"),
    "collection_has": ("collection", "path"),
}


class DslError(Exception):
    """A ``workflow.json`` is malformed or references something unknown — raised by
    ``parse_def`` (decode) and by the interpreter (a bad interpolation reference at
    run time). ``validate_def`` returns the *static* problems as strings instead, so
    ``save_workflow`` can hand them back to the agent to fix (manual §22, Q8)."""


# ─── schema (msgspec tagged union; ``type`` is the tag) ──────────────────────


class AgentStep(Struct, tag="agent", forbid_unknown_fields=True):
    """An LLM turn on the item (manual §5.1). ``out`` set ⇒ the model produces the file
    content as its reply and the step writes it (``agent_write_step``, gated on the file);
    ``out`` unset ⇒ a plain ``agent_step`` that needs an explicit ``check``."""

    prompt: str
    phase: str
    out: str = ""
    tools: list[str] = field(default_factory=list)
    check: dict[str, Any] | None = None
    retries: int = 0
    name: str = ""
    # #428 §1/§2: declared output fields (name → type). When set, the agent replies with
    # a JSON object; the step parses + records it as ``result.fields``, referenceable
    # downstream as ``{steps.<name>.<field>}``. The type values gain meaning in P2.
    outputs: dict[str, Any] = field(default_factory=dict)


class SandboxStep(Struct, tag="sandbox", forbid_unknown_fields=True):
    """A deterministic command in the sandbox, no LLM (manual §5.2) — the escape hatch
    for arbitrary author logic. Compute-only for a user workflow (no credential, Q4)."""

    run: str
    phase: str
    check: dict[str, Any] | None = None
    name: str = ""
    # #428 §1/§2: like AgentStep — when set, the script prints a JSON object to stdout
    # which the step parses into ``result.fields`` (referenceable downstream).
    outputs: dict[str, Any] = field(default_factory=dict)


class GateStep(Struct, tag="gate", forbid_unknown_fields=True):
    """A human gate (manual §10). The interpreter continues only on ``approve``; any
    other terminal choice (e.g. ``reject``) ends the run as ``{"status": choice}`` — no
    revise-loop in v1 (Q7). ``summary_from`` is a glob whose matched files are shown."""

    phase: str
    title: str
    summary_from: str = ""
    allow: list[str] = field(default_factory=lambda: ["approve", "reject"])


class CapabilityStep(Struct, tag="capability", forbid_unknown_fields=True):
    """A reliable, idempotent side-effect (manual §8) run under the captured user."""

    call: str
    phase: str
    collection: str = ""
    path: str = ""
    keys: list[str] = field(default_factory=list)
    title: str = ""
    body: str = ""
    # #419 create_entity: which entity type + the field args (values may interpolate).
    type_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


class MapStep(Struct, tag="map", forbid_unknown_fields=True):
    """The one loop (manual §11): bind ``as`` to each element of ``over`` and run ``do``
    per element. One level — ``do`` may not contain a ``map`` or a ``gate`` (Q7). ``over``
    is a glob string (sorted paths), a list-value reference like ``{steps.x.items}`` (array
    order), or ``{"range": <interp>}`` (indices ``0..n-1``) — #428 §4. ``key_by`` names the
    field to key list-of-object elements by (else the array position)."""

    over: str | dict[str, Any]
    phase: str
    as_: str = field(name="as", default="item")
    do: list[Step] = field(default_factory=list)
    key_by: str = ""


class SwitchStep(Struct, tag="switch", forbid_unknown_fields=True):
    """A data-driven branch (#428 §3): resolve ``on`` to a value, run the one ``cases``
    sequence keyed by it (else ``default``). ``default`` present (even ``[]``) ⇒ other
    values are a no-op; ``default`` absent ⇒ an unmatched value is a loud runtime error.
    ``on`` must be a single *stable* reference (config / inputs / a named step's field);
    the switch itself is pure control flow and is never journaled (§3.3)."""

    on: str
    phase: str
    cases: dict[str, list[Step]] = field(default_factory=dict)
    default: list[Step] | None = None


Step = AgentStep | SandboxStep | GateStep | CapabilityStep | MapStep | SwitchStep


class WorkflowDef(Struct, forbid_unknown_fields=True):
    """A parsed ``workflow.json`` — the declarative whole (manual §22)."""

    id: str
    title: str = ""
    phases: list[WorkflowPhase] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    schema_version: int = field(name="schema", default=1)
    description: str = ""
    tag: str = ""
    hint: str = ""


def parse_def(raw: bytes | str) -> WorkflowDef:
    """Decode ``workflow.json`` bytes into a ``WorkflowDef``. Raises ``DslError`` on
    malformed JSON, an unknown field, or a bad step ``type`` (msgspec gives a precise
    location, which we surface verbatim)."""
    try:
        return msgspec.json.decode(raw.encode() if isinstance(raw, str) else raw, type=WorkflowDef)
    except msgspec.ValidationError as e:  # the more specific subclass first (bad field/tag)
        raise DslError(str(e)) from e
    except msgspec.DecodeError as e:  # genuine JSON syntax error
        raise DslError(f"not valid JSON: {e}") from e


def build_manifest(d: WorkflowDef) -> WorkflowManifest:
    """The read-only manifest (title + phase skeleton, manual §12) the FE Run picker
    renders, derived from the DSL — so a DSL workflow needs no separate ``_profile.json``
    entry (it carries its own metadata)."""
    return WorkflowManifest(
        id=d.id,
        title=d.title,
        phases=list(d.phases),
        config=dict(d.config),
        description=d.description,
        tag=d.tag,
        hint=d.hint,
    )


# ─── interpolation (deterministic, async, no eval) ───────────────────────────

_TOKEN = re.compile(r"\{([^{}]+)\}")


def _stringify(val: Any) -> str:
    return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False, sort_keys=True)


async def _index(val: Any, seg: str, wf: WorkflowHandle) -> Any:
    """Read field ``seg`` off ``val``. A ``val`` that is a path to a ``.json`` file is
    read + parsed first — this is how the agent's recorded decision routes to a
    capability (the §8 decision→data→action split: ``{p.collection}`` reads ``p``)."""
    if isinstance(val, str) and val.lower().endswith(".json"):
        val = await wf.read_json(val)
    if isinstance(val, dict):
        if seg not in val:
            raise DslError(f"field {seg!r} not found in object")
        return val[seg]
    raise DslError(f"cannot read field {seg!r} from a {type(val).__name__}")


async def _lookup_step(parts: list[str], ns: dict[str, Any], wf: WorkflowHandle) -> Any:
    """Resolve ``{steps.<name>.<field>}`` (#428 §1.1) — read the named step's journal
    entry at the *current scope key* (top-level ⇒ ``""``, a map element ⇒ its key) and
    index ``result.fields``. Same-file-as-journal: no parallel store."""
    if len(parts) < 2:
        raise DslError("{steps} needs a step name")
    name = parts[1]
    record = await wf.read_json(_artifact_path(wf, name, ns.get("__key__", "")))
    result = record.get("result") if isinstance(record, dict) else None
    fields = result.get("fields") if isinstance(result, dict) else None
    if not isinstance(fields, dict):
        raise DslError(f"step {name!r} has no output fields to reference")
    val: Any = fields
    for seg in parts[2:]:
        val = await _index(val, seg, wf)
    return val


async def _lookup(expr: str, ns: dict[str, Any], wf: WorkflowHandle) -> Any:
    parts = expr.split(".")
    if parts[0] == "steps":  # #428 §1: the named-output reference namespace
        return await _lookup_step(parts, ns, wf)
    if parts[0] not in ns:
        raise DslError(f"unknown variable {{{expr}}}")
    val: Any = ns[parts[0]]
    for seg in parts[1:]:
        val = await _index(val, seg, wf)
    return val


# #428 §2: the declarable output types. ``list``/``obj`` are shallow (no nested schema);
# ``enum`` narrows a scalar's value set.
_OUTPUT_TYPES = ("str", "int", "float", "bool", "list", "obj")


def _spec_type_enum(spec: Any) -> tuple[Any, Any]:
    """Split an ``outputs`` field spec into ``(type, enum)`` — a bare ``"str"`` or a
    ``{"type": "str", "enum": [...]}`` object (#428 §2.1)."""
    if isinstance(spec, dict):
        return spec.get("type"), spec.get("enum")
    return spec, None


def _field_type_ok(value: Any, tname: Any) -> bool:
    """Does ``value`` satisfy the declared type ``tname``? JSON ``true``/``false`` are
    Python ``bool`` (a subclass of ``int``), so ``int``/``float`` explicitly exclude
    ``bool`` and ``float`` accepts a whole number (#428 §2)."""
    if tname == "str":
        return isinstance(value, str)
    if tname == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if tname == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if tname == "bool":
        return isinstance(value, bool)
    if tname == "list":
        return isinstance(value, list)
    return isinstance(value, dict)  # obj


def _outputs_check(outputs: dict[str, Any]) -> Check:
    """The implicit gate for a step that declares ``outputs`` (#428 §1.2/§2.2): the reply
    must parse into a JSON object whose declared fields are present and match their
    type + optional ``enum``. A failure feeds its reason back into the step's retry."""

    async def check(_wf: WorkflowHandle, result: Any) -> CheckResult:
        fields = result.get("fields") if isinstance(result, dict) else None
        if not isinstance(fields, dict):
            return CheckResult(False, f"reply must be a JSON object with keys {sorted(outputs)}")
        for name, spec in outputs.items():
            if name not in fields:
                return CheckResult(False, f"reply is missing field {name!r}")
            tname, enum = _spec_type_enum(spec)
            value = fields[name]
            if not _field_type_ok(value, tname):
                return CheckResult(
                    False, f"field {name!r} must be {tname}, got {type(value).__name__}"
                )
            if enum is not None and value not in enum:
                return CheckResult(False, f"field {name!r} must be one of {enum}, got {value!r}")
        return CheckResult(True)

    return check


async def _resolve(template: Any, ns: dict[str, Any], wf: WorkflowHandle) -> Any:
    """Resolve ``{var}`` / ``{var.field}`` references against ``ns`` (``config`` /
    ``inputs`` + the active map var). A template that is *exactly* one ``{expr}`` returns
    the resolved value (may be a list/dict — e.g. ``allowed`` ← ``{config.collections}``);
    otherwise it string-substitutes. No arbitrary expressions, no eval."""
    if not isinstance(template, str):
        return template
    whole = _TOKEN.fullmatch(template)
    if whole is not None:
        return await _lookup(whole.group(1).strip(), ns, wf)
    out: list[str] = []
    last = 0
    for m in _TOKEN.finditer(template):
        out.append(template[last : m.start()])
        out.append(_stringify(await _lookup(m.group(1).strip(), ns, wf)))
        last = m.end()
    out.append(template[last:])
    return "".join(out)


# ─── interpreter ─────────────────────────────────────────────────────────────


class _Stop(Exception):
    """A gate ended the run (non-``approve`` choice) — caught by ``build_run`` to return
    ``{"status": choice}`` (manual §10 produce→review→commit: a reject commits nothing)."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _safe_key(path: str) -> str:
    """A map element's journal key from its path — same convention as ``ingest`` (§9)."""
    return path.lstrip("/").replace("/", "_")


def _keyed_list(step: MapStep, items: list[Any]) -> list[tuple[Any, str]]:
    """Materialise a list-value ``over`` into ``(element, key)`` pairs in array order
    (#428 §4.2): keyed by ``key_by`` field (must be an object with that field) else the
    array position. A ``key_by`` collision is loud — never a silent cache-skip."""
    elements: list[tuple[Any, str]] = []
    for i, item in enumerate(items):
        if step.key_by:
            if not isinstance(item, dict) or step.key_by not in item:
                raise StepFailed(
                    f"map key_by {step.key_by!r} needs object elements carrying that field"
                )
            key = _safe_key(str(item[step.key_by]))
        else:
            key = str(i)
        elements.append((item, key))
    keys = [k for _, k in elements]
    if len(set(keys)) != len(keys):
        raise StepFailed(f"map key_by {step.key_by!r} is not unique across elements")
    return elements


async def _map_elements(
    step: MapStep, ns: dict[str, Any], wf: WorkflowHandle
) -> list[tuple[Any, str]]:
    """Resolve ``over`` into ``(element, key)`` pairs (#428 §4): a ``{"range": n}`` object
    ⇒ indices ``0..n-1``; a value that resolves to a list ⇒ array order; anything else is a
    glob string ⇒ sorted matched paths."""
    over = step.over
    if isinstance(over, dict):  # range form
        raw = await _resolve(over.get("range", ""), ns, wf)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise StepFailed(f"map 'over' range must be an integer, got {raw!r}") from None
        return [(i, str(i)) for i in range(n)]
    resolved = await _resolve(over, ns, wf)
    if isinstance(resolved, list):  # list-value form
        return _keyed_list(step, resolved)
    return [(p, _safe_key(p)) for p in await wf.glob(resolved)]  # glob form


async def _build_check(spec: dict[str, Any], ns: dict[str, Any], wf: WorkflowHandle) -> Check:
    ((name, args),) = spec.items()
    if name == "file_nonempty":
        return file_nonempty(await _resolve(args["path"], ns, wf))
    if name == "choice_in":
        return choice_in(
            await _resolve(args["path"], ns, wf),
            key=args["key"],
            allowed=await _resolve(args["allowed"], ns, wf),
        )
    return collection_has(
        await _resolve(args["collection"], ns, wf),
        await _resolve(args["path"], ns, wf),
    )


async def _gate_summary(wf: WorkflowHandle, step: GateStep, ns: dict[str, Any]) -> Any:
    if not step.summary_from:
        return ""
    summary: dict[str, Any] = {}
    for p in await wf.glob(await _resolve(step.summary_from, ns, wf)):
        summary[p] = await wf.read_json(p) if p.lower().endswith(".json") else await wf.read_text(p)
    return summary


async def _exec_capability(wf: WorkflowHandle, step: CapabilityStep, ns: dict[str, Any]) -> None:
    if step.call == "ingest_to_collection":
        await wf.ingest_to_collection(
            await _resolve(step.collection, ns, wf),
            await _resolve(step.path, ns, wf),
            phase=step.phase,
        )
    elif step.call == "create_entity":  # #419 — same numbering pipeline, no raw write
        resolved = {
            k: (await _resolve(v, ns, wf) if isinstance(v, str) else v)
            for k, v in step.args.items()
        }
        await wf.create_entity(await _resolve(step.type_name, ns, wf), resolved, phase=step.phase)
    else:  # upsert_context_card (the only other allowed call; validated upstream)
        await wf.upsert_context_card(
            await _resolve(step.collection, ns, wf),
            [await _resolve(k, ns, wf) for k in step.keys],
            title=await _resolve(step.title, ns, wf),
            body=await _resolve(step.body, ns, wf),
            phase=step.phase,
        )


async def _exec_step(
    wf: WorkflowHandle,
    step: Step,
    ns: dict[str, Any],
    key: str,
    failures: list[dict[str, str]],
) -> None:
    if isinstance(step, MapStep):
        elements = await _map_elements(step, ns, wf)

        async def _one(elem: tuple[Any, str]) -> None:
            value, ekey = elem
            # #428 §1.1: an inner ``{steps.x.f}`` resolves at this element's key.
            sub = {**ns, step.as_: value, "__key__": ekey}
            for inner in step.do:
                await _exec_step(wf, inner, sub, ekey, failures)

        failures.extend(await wf.map(_one, elements))
        return
    if isinstance(step, SwitchStep):
        # #428 §3: pure control flow — resolve ``on``, run the one matching case (or
        # ``default``); no journal, so replay re-picks the same case from stable inputs.
        case_key = _stringify(await _resolve(step.on, ns, wf))
        if case_key in step.cases:
            seq = step.cases[case_key]
        elif step.default is not None:
            seq = step.default
        else:  # §3.4: unmatched + no default ⇒ loud (element-level inside a map)
            raise StepFailed(
                f"switch on {step.on} got {case_key!r}: no matching case and no default"
            )
        for inner in seq:
            await _exec_step(wf, inner, ns, key, failures)
        return
    if isinstance(step, GateStep):
        decision = await human_gate(
            wf,
            phase=step.phase,
            title=await _resolve(step.title, ns, wf),
            summary=await _gate_summary(wf, step, ns),
            allow=step.allow,
        )
        if decision.choice != "approve":
            raise _Stop(decision.choice)
        return
    if isinstance(step, CapabilityStep):
        await _exec_capability(wf, step, ns)
        return
    if isinstance(step, SandboxStep):
        # #428 §1.2: ``outputs`` ⇒ parse stdout JSON into result.fields, gated on it.
        check = (
            _outputs_check(step.outputs)
            if step.outputs
            else (await _build_check(step.check, ns, wf) if step.check else None)
        )
        await sandbox_node(
            wf,
            run=await _resolve(step.run, ns, wf),
            phase=step.phase,
            check=check,
            name=step.name or None,
            key=key,
            outputs=step.outputs or None,
        )
        return
    # AgentStep
    prompt = await _resolve(step.prompt, ns, wf)
    tools = step.tools or None
    # #428 §1.2: an ``outputs`` step parses its reply into result.fields, gated on it;
    # otherwise fall back to the author's ``check``.
    if step.outputs:
        check: Check | None = _outputs_check(step.outputs)
    elif step.check:
        check = await _build_check(step.check, ns, wf)
    else:
        check = None
    if step.out or step.outputs:
        await agent_write_step(
            wf,
            prompt=prompt,
            phase=step.phase,
            out=await _resolve(step.out, ns, wf) if step.out else "",
            tools=tools,
            name=step.name or None,
            key=key,
            retries=step.retries,
            check=check,
            outputs=step.outputs or None,
        )
    else:  # plain agent_step — ``check`` is required (validate_def guarantees it)
        assert check is not None
        await agent_step(
            wf,
            prompt=prompt,
            phase=step.phase,
            check=check,
            tools=tools,
            name=step.name or None,
            key=key,
            retries=step.retries,
        )


def build_run(d: WorkflowDef):
    """A ``ProfileRun`` (``async def run(wf, inputs)``) that interprets ``d`` over the
    existing step primitives — the orchestrator runs it exactly like a hand-written one."""

    async def run(wf: WorkflowHandle, inputs: Any) -> dict[str, Any]:
        ns: dict[str, Any] = {
            "config": d.config,
            "inputs": inputs if inputs is not None else {},
            "__key__": "",  # #428 §1.1: the current scope key for {steps.x.f}
        }
        failures: list[dict[str, str]] = []
        try:
            for step in d.steps:
                await _exec_step(wf, step, ns, "", failures)
        except _Stop as stop:
            return {"status": stop.status}
        result: dict[str, Any] = {"status": "done"}
        if failures:
            result["failures"] = failures
        return result

    return run


# ─── static validation (manual §22, Q8) ──────────────────────────────────────


def _check_step_ref(
    expr: str, steps_seen: dict[str, dict[str, Any]], where: str, errs: list[str]
) -> None:
    """Validate a ``{steps.<name>.<field>}`` reference (#428 §1.5): the step must be
    declared *earlier in the same scope* (``steps_seen`` accumulates in order, so a
    forward reference is simply not-yet-seen ⇒ 'unknown step'), and the field must be
    one it declares in ``outputs``."""
    parts = expr.split(".")
    if len(parts) < 2:
        errs.append(f"{where}: {{{expr}}} needs a step name")
        return
    name = parts[1]
    if name not in steps_seen:
        errs.append(f"{where}: {{{expr}}} references unknown step {name!r}")
        return
    if len(parts) >= 3 and parts[2] not in steps_seen[name]:
        errs.append(f"{where}: {{{expr}}} — step {name!r} has no output field {parts[2]!r}")


def _check_interp(
    value: Any,
    scope: set[str],
    where: str,
    errs: list[str],
    steps_seen: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Flag any ``{root...}`` whose root variable isn't in scope (catches typos at save
    time). When ``steps_seen`` is given, also resolve ``{steps.x.f}`` references against
    it (#428 §1.5). Recurses into the lists/dicts a check spec carries."""
    if isinstance(value, str):
        for m in _TOKEN.finditer(value):
            expr = m.group(1).strip()
            root = expr.split(".")[0]
            if root not in scope:
                errs.append(f"{where}: {{{expr}}} references unknown variable {root!r}")
            elif root == "steps" and steps_seen is not None:
                _check_step_ref(expr, steps_seen, where, errs)
    elif isinstance(value, list):
        for v in value:
            _check_interp(v, scope, where, errs, steps_seen)
    elif isinstance(value, dict):
        for v in value.values():
            _check_interp(v, scope, where, errs, steps_seen)


def _validate_outputs(outputs: dict[str, Any], where: str, errs: list[str]) -> None:
    """Statically check an ``outputs`` declaration (#428 §2): each field spec is a valid
    type string or ``{"type": ..., "enum": [...]}``; ``enum`` is scalar-only."""
    for name, spec in outputs.items():
        if isinstance(spec, str):
            tname, enum = spec, None
        elif isinstance(spec, dict) and "type" in spec:
            tname, enum = spec.get("type"), spec.get("enum")
        else:
            errs.append(
                f"{where}: output {name!r} must be a type string or {{'type': ..., 'enum': ...}}"
            )
            continue
        if tname not in _OUTPUT_TYPES:
            errs.append(
                f"{where}: output {name!r} has unknown output type {tname!r} "
                f"(one of {list(_OUTPUT_TYPES)})"
            )
        if enum is not None and tname in ("list", "obj"):
            errs.append(f"{where}: output {name!r} enum is only allowed on scalar types")


def _validate_check(
    spec: dict[str, Any],
    scope: set[str],
    where: str,
    errs: list[str],
    steps_seen: dict[str, dict[str, Any]] | None = None,
) -> None:
    if len(spec) != 1:
        errs.append(f"{where}: a check must name exactly one of {list(_CHECKS)}")
        return
    ((name, args),) = spec.items()
    if name not in _CHECKS:
        errs.append(f"{where}: unknown check {name!r} (one of {list(_CHECKS)})")
        return
    if not isinstance(args, dict):
        errs.append(f"{where}: check {name!r} needs an object of arguments")
        return
    for req in _CHECK_REQUIRED[name]:
        if req not in args:
            errs.append(f"{where}: check {name!r} is missing {req!r}")
    _check_interp(args, scope, where, errs, steps_seen)


def _validate_step(
    step: Step,
    where: str,
    declared: set[str],
    scope: set[str],
    tool_ceiling: set[str] | None,
    capabilities: tuple[str, ...],
    errs: list[str],
    steps_seen: dict[str, dict[str, Any]],
    *,
    top: bool,
    depth: int = 0,
) -> None:
    # Q7 / #428 §3.2: in a map-element context (``not top``) a map or gate is rejected —
    # the ban propagates through a nested switch (whose cases inherit ``top``).
    if not top and isinstance(step, MapStep | GateStep):
        errs.append(f"{where}: a {step.__struct_config__.tag} cannot be nested in a map (Q7)")
        return
    if step.phase not in declared:
        errs.append(f"{where}: phase {step.phase!r} is not declared in 'phases'")
    if isinstance(step, MapStep):
        # #428 §4: ``over`` is a glob string, a list-value reference, or {"range": <ref>}.
        if isinstance(step.over, dict):
            if set(step.over) != {"range"} or not isinstance(step.over.get("range"), str):
                errs.append(f"{where}: map 'over' object must be {{'range': <reference>}}")
            else:
                _check_interp(step.over["range"], scope, where, errs, steps_seen)
        elif not step.over:
            errs.append(f"{where}: map needs a non-empty 'over' glob")
        else:
            _check_interp(step.over, scope, where, errs, steps_seen)
        if not step.as_:
            errs.append(f"{where}: map needs a non-empty 'as'")
        if not step.do:
            errs.append(f"{where}: map 'do' is empty")
        # #428 §5.3: a map's ``do`` is its own naming subdomain — a fresh steps_seen so an
        # inner ``{steps.x.f}`` can only reference a sibling declared earlier in the loop.
        _validate_steps(
            step.do,
            f"{where}.do",
            declared,
            scope | {step.as_},
            tool_ceiling,
            capabilities,
            errs,
            top=False,
            depth=depth,
        )
        return
    if isinstance(step, SwitchStep):
        _validate_switch(
            step,
            where,
            declared,
            scope,
            tool_ceiling,
            capabilities,
            errs,
            steps_seen,
            top=top,
            depth=depth,
        )
        return
    if isinstance(step, GateStep):
        if not step.title:
            errs.append(f"{where}: gate needs a 'title'")
        if not step.allow:
            errs.append(f"{where}: gate 'allow' is empty")
        _check_interp(step.title, scope, where, errs, steps_seen)
        return
    if isinstance(step, CapabilityStep):
        if step.call not in capabilities:
            errs.append(
                f"{where}: capability {step.call!r} is not allowed (one of {list(capabilities)})"
            )
        else:
            for req in _CAP_REQUIRED[step.call]:
                if not getattr(step, req):
                    errs.append(f"{where}: capability {step.call!r} needs {req!r}")
        _check_interp(
            [step.collection, step.path, step.title, step.body, step.keys, step.args],
            scope,
            where,
            errs,
            steps_seen,
        )
        return
    if isinstance(step, SandboxStep):
        if not step.run:
            errs.append(f"{where}: sandbox needs a non-empty 'run'")
        _check_interp(step.run, scope, where, errs, steps_seen)
        if step.check is not None:
            _validate_check(step.check, scope, where, errs, steps_seen)
        _validate_outputs(step.outputs, where, errs)
        return
    # AgentStep
    if not step.prompt:
        errs.append(f"{where}: agent needs a 'prompt'")
    if not step.out and not step.outputs and step.check is None:
        errs.append(
            f"{where}: an agent step without 'out' needs a 'check' or 'outputs' "
            "(a gate is mandatory, §5.1)"
        )
    if step.retries < 0:
        errs.append(f"{where}: retries cannot be negative")
    if tool_ceiling is not None:
        for t in step.tools:
            if t not in tool_ceiling:
                errs.append(f"{where}: tool {t!r} is outside the profile's allowed tools")
    _check_interp([step.prompt, step.out, step.tools], scope, where, errs, steps_seen)
    if step.check is not None:
        _validate_check(step.check, scope, where, errs, steps_seen)
    _validate_outputs(step.outputs, where, errs)


_SWITCH_MAX_DEPTH = 32  # #428 §3.2: a defensive cap on nested switches (not expressivity)


def _validate_switch_enum(
    step: SwitchStep, steps_seen: dict[str, dict[str, Any]], where: str, errs: list[str]
) -> None:
    """When ``on`` addresses a named step's ``enum`` field (#428 §3.4): every case must be
    a member of the enum, and (with no ``default``) every enum value must be covered —
    else an unmatched value would fail loudly at run time."""
    m = _TOKEN.fullmatch(step.on.strip())
    parts = m.group(1).strip().split(".") if m else []
    if len(parts) < 3 or parts[0] != "steps":
        return
    name, fieldname = parts[1], parts[2]
    if name not in steps_seen or fieldname not in steps_seen[name]:
        return  # existence already reported by _check_interp
    _, enum = _spec_type_enum(steps_seen[name][fieldname])
    if enum is None:
        return
    allowed = {_stringify(v) for v in enum}
    for cval in step.cases:
        if cval not in allowed:
            errs.append(
                f"{where}: switch case {cval!r} is not in the enum of {'.'.join(parts[1:])}"
            )
    if step.default is None:
        missing = allowed - set(step.cases)
        if missing:
            errs.append(
                f"{where}: switch does not cover enum value(s) {sorted(missing)} and has no "
                "default (add the case(s) or a 'default')"
            )


def _validate_switch(
    step: SwitchStep,
    where: str,
    declared: set[str],
    scope: set[str],
    tool_ceiling: set[str] | None,
    capabilities: tuple[str, ...],
    errs: list[str],
    steps_seen: dict[str, dict[str, Any]],
    *,
    top: bool,
    depth: int,
) -> None:
    if depth >= _SWITCH_MAX_DEPTH:
        errs.append(f"{where}: switch nesting too deep (max {_SWITCH_MAX_DEPTH})")
        return
    if not step.on:
        errs.append(f"{where}: switch needs a non-empty 'on'")
    elif _TOKEN.fullmatch(step.on.strip()) is None:
        errs.append(f"{where}: switch 'on' must be a single stable reference like {{steps.x.f}}")
    else:
        _check_interp(step.on, scope, where, errs, steps_seen)
        _validate_switch_enum(step, steps_seen, where, errs)
    if not step.cases:
        errs.append(f"{where}: switch needs at least one case")
    # #428 §3.2: a case sequence inherits the switch's context (``top``) — a top-level
    # switch's cases may hold gate/map; a switch nested in a map propagates the ban.
    for cval, seq in step.cases.items():
        _validate_steps(
            seq,
            f"{where}.cases[{cval}]",
            declared,
            scope,
            tool_ceiling,
            capabilities,
            errs,
            top=top,
            depth=depth + 1,
        )
    if step.default is not None:
        _validate_steps(
            step.default,
            f"{where}.default",
            declared,
            scope,
            tool_ceiling,
            capabilities,
            errs,
            top=top,
            depth=depth + 1,
        )


def _register_step(
    step: Step, steps_seen: dict[str, dict[str, Any]], where: str, errs: list[str]
) -> None:
    """After a step is validated, record its ``name`` → declared output field names so a
    later sibling can reference it (#428 §1.5). Names are unique within a scope."""
    if not isinstance(step, AgentStep | SandboxStep) or not step.name:
        return
    if step.name in steps_seen:
        errs.append(f"{where}: duplicate step name {step.name!r}")
    else:
        # #428 §1.5/§3.4: keep the whole ``outputs`` declaration so a reference can be
        # checked for field existence AND a switch can check its cases against an enum.
        steps_seen[step.name] = dict(step.outputs)


def _validate_steps(
    steps: list[Step],
    where_prefix: str,
    declared: set[str],
    scope: set[str],
    tool_ceiling: set[str] | None,
    capabilities: tuple[str, ...],
    errs: list[str],
    *,
    top: bool,
    depth: int = 0,
) -> None:
    """Validate an ordered step list in one naming scope: each step is checked against
    the names declared *before* it (``steps_seen`` accumulates in order), then registers
    its own name (#428 §1.5). ``depth`` bounds nested switches (§3.2)."""
    steps_seen: dict[str, dict[str, Any]] = {}
    for i, step in enumerate(steps):
        where = f"{where_prefix}[{i}]"
        _validate_step(
            step,
            where,
            declared,
            scope,
            tool_ceiling,
            capabilities,
            errs,
            steps_seen,
            top=top,
            depth=depth,
        )
        _register_step(step, steps_seen, where, errs)


def validate_def(
    d: WorkflowDef,
    *,
    tool_ceiling: set[str] | None = None,
    capabilities: tuple[str, ...] = CAPABILITIES,
) -> list[str]:
    """Static problems with a parsed DSL, as human-readable strings (empty ⇒ valid).
    ``save_workflow`` hands these back to the agent to fix (manual §22, Q8); package
    discovery fails loud on them. ``tool_ceiling`` (``None`` ⇒ skip) clamps an agent
    step's ``tools`` to the profile's allowed set."""
    errs: list[str] = []
    if d.schema_version != 1:
        errs.append(f"unsupported schema version {d.schema_version} (only 1)")
    if not d.id:
        errs.append("workflow 'id' is empty")
    declared = {p.id for p in d.phases}
    if "" in declared:
        errs.append("a phase is missing its 'id'")
    if not d.steps:
        errs.append("workflow has no steps")
    _validate_steps(
        d.steps,
        "steps",
        declared,
        {"config", "inputs", "steps"},  # #428 §1: the named-output reference namespace
        tool_ceiling,
        capabilities,
        errs,
        top=True,
    )
    return errs
