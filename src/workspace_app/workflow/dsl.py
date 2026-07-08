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

from .checks import ARTIFACT_KINDS, choice_in, collection_has, file_nonempty
from .engine import Check, CheckResult, StepFailed, _artifact_path
from .gate import _decision_path, human_gate
from .handle import WorkflowHandle
from .manifest import WorkflowManifest, WorkflowPhase
from .steps import agent_write_step, sandbox_node

# The capability calls a user DSL may invoke (manual §22, Q4). Each maps to a
# ``WorkflowHandle`` method that runs under the captured user's authz; a ``sandbox``
# step gets no credential, so reliable side-effects only ever go through these.
CAPABILITIES = (
    "ingest_to_collection",
    "upsert_context_card",
    "create_entity",
    "update_entity",
    "send_notification",
)
_CAP_REQUIRED: dict[str, tuple[str, ...]] = {
    "ingest_to_collection": ("collection", "path"),
    "upsert_context_card": ("collection", "keys"),
    "create_entity": ("type_name",),
    # #429 P2: update by (type + number); ``args`` is the merge-patch (fields → values).
    "update_entity": ("type_name", "number"),
}
# #435 P5: send_notification takes its recipient/topic/title/body in ``args`` (like
# create_entity's entity fields); these keys are required there.
_CAP_REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "send_notification": ("recipient", "topic"),
}
# #435 决议4: a *non-idempotent* capability's ``on_duplicate`` policy set is a
# per-capability interface — defined by the capability, NOT a strategy-layer default that
# other capabilities inherit. Absent ⇒ the capability is idempotent and takes no
# ``on_duplicate`` (setting one is a static error). ``create_new`` (M2 token) is live as of
# P7 — the per-invocation ``run_id`` mints a fresh entity each separate invocation.
_CAP_ON_DUPLICATE: dict[str, tuple[str, ...]] = {
    "create_entity": ("update", "skip", "create_new"),
}
# #435 P8: send_notification's per-window policy set — the periods #429's ``window_key``
# buckets by. Absent from a capability ⇒ it takes no ``window`` (setting one is a static
# error), same shape as ``_CAP_ON_DUPLICATE``.
_CAP_WINDOW: dict[str, tuple[str, ...]] = {
    "send_notification": ("daily", "weekly", "monthly"),
}
# #435: a capability's output fields are FIXED by the capability (owner-defined), not
# author-declared — referenceable downstream as ``{steps.<name>.<field>}``. A named
# non-idempotent capability registers these so a reference validates statically. A
# capability in this map must carry a ``name`` (its stable dedup identity, §P2).
_CAP_OUTPUTS: dict[str, dict[str, Any]] = {
    "create_entity": {"number": "int", "created": "bool", "action": "str"},
    "send_notification": {"sent": "bool", "action": "str", "notification_id": "str"},
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
    # plan §2.3: the artifact format of a channel-P ``out`` — its default gate is
    # ``artifact_valid(out, kind)`` (structured kinds PARSE-validate; prose kinds check
    # non-empty). Defaults to ``text`` (non-empty) when ``out`` is set without a ``kind``.
    kind: str = ""
    # plan §2.3 (L2, P3): a producer-declared structural contract on the prose ``out`` —
    # ``contains`` (required substrings/headings) + ``min_length`` — folded into the default
    # gate so a missing section fails and retries. Only valid on a channel-P (``out``) step.
    requires: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    check: dict[str, Any] | None = None
    retries: int = 0
    name: str = ""
    # #428 §1/§2: declared output fields (name → type). When set, the agent replies with
    # a JSON object; the step parses + records it as ``result.fields``, referenceable
    # downstream as ``{steps.<name>.<field>}``. The type values gain meaning in P2.
    outputs: dict[str, Any] = field(default_factory=dict)
    # #429 P1: files this turn DEPENDS on. The engine folds their content fingerprint into
    # the input-hash so editing a declared source re-runs the step (interpolation allowed).
    reads: list[str] = field(default_factory=list)
    # #429 P1 rule 3: opt out of the journal skip — always re-run (an honest 'always fresh'
    # for a step whose inputs the author can't fingerprint).
    cache: bool = True


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
    # #429 P1: files this command DEPENDS on. The engine folds their content fingerprint
    # into the input-hash so editing a declared file re-runs the step (a bare path in
    # ``run`` would skip on a content-only change). Interpolation allowed.
    reads: list[str] = field(default_factory=list)
    # #429 P1 rule 3: opt out of the journal skip — always re-run.
    cache: bool = True


class GateStep(Struct, tag="gate", forbid_unknown_fields=True):
    """A human gate (manual §10). The interpreter continues only on ``approve``; a
    terminal choice (e.g. ``reject``) ends the run as ``{"status": choice}``.
    ``summary_from`` is a glob whose matched files are shown.

    #428 §6: a named gate whose ``allow`` holds ``revise`` bounces the run back to
    ``revise_to`` (a top-level step declared before it) carrying the human's feedback,
    exposed as ``{steps.<name>.feedback}`` — the one legal forward reference. The re-drive
    is data-driven: the feedback folds into the target's input-hash so §9 re-runs it (and
    everything downstream) with no per-run bookkeeping."""

    phase: str
    title: str
    summary_from: str = ""
    allow: list[str] = field(default_factory=lambda: ["approve", "reject"])
    name: str = ""
    revise_to: str = ""


class CapabilityStep(Struct, tag="capability", forbid_unknown_fields=True):
    """A reliable side-effect (manual §8) run under the captured user. An idempotent
    capability (ingest/upsert) needs nothing more; a *non-idempotent* one (create_entity,
    #435) additionally declares a ``name`` — its stable dedup identity, its *site* — so a
    re-run of the same site (e.g. a gate revise that changes the content) self-dedups
    instead of double-creating, and its ``on_duplicate`` picks the duplicate action from
    the capability's own policy set (``_CAP_ON_DUPLICATE``)."""

    call: str
    phase: str
    collection: str = ""
    path: str = ""
    keys: list[str] = field(default_factory=list)
    title: str = ""
    body: str = ""
    # #419 create_entity: which entity type + the field args (values may interpolate).
    # #429 P2 update_entity: ``type_name`` + ``number`` identify the record, ``args`` is
    # the merge-patch. ``number`` is a literal int or an interpolation ref (``{q.n}``).
    type_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    number: str | int = ""
    # #435: a non-idempotent capability's stable dedup identity — referenceable as
    # {steps.<name>.<field>} — and its per-capability on_duplicate policy.
    name: str = ""
    on_duplicate: str = ""
    # #435 P8: send_notification's per-window dedup policy — "" (once-ever) or
    # daily/weekly/monthly (once per period). A per-capability policy, like on_duplicate.
    window: str = ""


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
    # #428 §5: a named map collects its designated inner step's fields into
    # ``{steps.<name>.outputs}`` (a per-element list). ``collect`` picks that inner step
    # when more than one declares ``outputs``.
    name: str = ""
    collect: str = ""
    # #429 P5: the author's requested parallelism for this map. The EFFECTIVE cap is
    # ``min(request, wf.turn_concurrency)`` — a REQUEST throttled by the model backend's
    # real concurrency (a single local model → ~1), not a guarantee. 0 ⇒ use the backend
    # ceiling (or the engine default when unset).
    concurrency: int = 0


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
    path = _artifact_path(wf, name, ns.get("__key__", ""))
    if not await wf.exists(path):
        # #428 §6: a gate's feedback is the one legal forward reference — before the gate
        # has run (or before any revise) it resolves to "". Every other reference is to a
        # step that already ran (validate_def rejects non-gate forward references).
        if parts[2:] == ["feedback"]:
            return ""
        raise DslError(f"step {name!r} has not produced output fields to reference yet")
    record = await wf.read_json(path)
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


async def _resolve_reads(
    reads: list[str], ns: dict[str, Any], wf: WorkflowHandle
) -> list[str] | None:
    """Resolve a step's ``reads`` declarations (#429 P1) — interpolate each entry
    (``{config.dir}/*.log`` → ``logs/*.log``) into a concrete path/glob string. Returns
    ``None`` for an empty ``reads`` so the adapter leaves the input-hash untouched."""
    if not reads:
        return None
    return [_stringify(await _resolve(r, ns, wf)) for r in reads]


async def _resolve_number(number: str | int, ns: dict[str, Any], wf: WorkflowHandle) -> int:
    """Resolve an ``update_entity`` capability's ``number`` (#429 P2) — a literal int or an
    interpolation ref (``{q.n}``) — to the concrete entity number."""
    value = await _resolve(number, ns, wf) if isinstance(number, str) else number
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DslError(f"update_entity 'number' must resolve to an integer, got {value!r}") from exc


# ─── interpreter ─────────────────────────────────────────────────────────────


class _Stop(Exception):
    """A gate ended the run (non-``approve`` choice) — caught by ``build_run`` to return
    ``{"status": choice}`` (manual §10 produce→review→commit: a reject commits nothing)."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class _Revise(Exception):
    """A gate chose ``revise`` (#428 §6) — caught by ``build_run`` to re-drive the run
    from the top. The gate has already recorded its feedback (so ``{steps.<gate>.feedback}``
    now resolves) and cleared its own decision; the re-drive re-runs the ``revise_to``
    target via §9 hash-chaining and re-pauses at the gate."""


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


_DEFAULT_MAP_CONCURRENCY = 8  # the engine default when neither author nor backend sets one


def _map_concurrency(step: MapStep, wf: WorkflowHandle) -> int:
    """The effective parallel-turn cap for a map (#429 P5): the author's ``concurrency``
    request (or the backend/engine default when unset), throttled by the model backend's
    real concurrency (``wf.turn_concurrency``). ``min(request, backend)`` — a REQUEST, not
    a guarantee: a single local model (backend ≈ 1) degrades a ``concurrency: 8`` map to
    serial without touching the workflow, while a hosted pool lets it run wide."""
    request = step.concurrency or wf.turn_concurrency or _DEFAULT_MAP_CONCURRENCY
    if wf.turn_concurrency is not None:
        return min(request, wf.turn_concurrency)
    return request


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


def _named_steps_in(steps: list[Step]) -> dict[str, Step]:
    """All ``name``-bearing agent/sandbox steps reachable in ``steps`` — recursing into a
    switch's cases (#428 §5), so a collect target nested under a switch is still found."""
    found: dict[str, Step] = {}
    for s in steps:
        if isinstance(s, AgentStep | SandboxStep) and s.name:
            found[s.name] = s
        elif isinstance(s, SwitchStep):
            seqs = list(s.cases.values()) + ([s.default] if s.default is not None else [])
            for seq in seqs:
                found.update(_named_steps_in(seq))
    return found


def _collect_name(step: MapStep) -> str:
    """Which inner step's fields ``{steps.<map>.outputs}`` collects (#428 §5.1): the
    explicit ``collect``; else the unique ``outputs``-declaring step; else (none declares
    outputs) the unique named step. ``""`` when ambiguous — a named map then fails static
    validation."""
    if step.collect:
        return step.collect
    named = _named_steps_in(step.do)
    with_outputs = [n for n, s in named.items() if getattr(s, "outputs", None)]
    if len(with_outputs) == 1:
        return with_outputs[0]
    if not with_outputs and len(named) == 1:
        return next(iter(named))
    return ""


def _inner_journal_names(steps: list[Step]) -> set[str]:
    """The journal names of a map's inner agent/sandbox steps — the ones keyed by the
    ELEMENT key (``name or phase``), recursing into a switch's cases. Capabilities are
    excluded: they key by content (card key / args digest), never the element key, so
    they must not be pruned by element key (#429 P4)."""
    names: set[str] = set()
    for s in steps:
        if isinstance(s, AgentStep | SandboxStep):
            names.add(s.name or s.phase)
        elif isinstance(s, SwitchStep):
            seqs = list(s.cases.values()) + ([s.default] if s.default is not None else [])
            for seq in seqs:
                names |= _inner_journal_names(seq)
    return names


async def _gc_map_orphans(wf: WorkflowHandle, step: MapStep, current_keys: set[str]) -> None:
    """Prune a map's inner per-element journal artifacts for element keys no longer in the
    current set (#429 P4). Runs when the map re-runs, right after the element set resolves,
    so cleanup is tied to the natural moment the set changes — no standing GC sweep. Only
    deletes keys that genuinely left the set (a key still present, or a set that grew, is
    untouched), so a transient glob shrink at worst re-computes that element next time."""
    for jname in _inner_journal_names(step.do):
        prefix = f"{wf.journal_dir.lstrip('/')}/step_{jname}"
        for path in await wf.glob([f"{prefix}/*.json"]):
            key = path.rsplit("/", 1)[-1].removesuffix(".json")
            if key not in current_keys:
                await wf.delete(path)


def _collected_value(result: Any) -> Any:
    """One element's contribution to ``.outputs``: its collected step's ``fields`` (an
    ``outputs`` step), else its ``out`` path (a write step), else ``None`` (§5.1 degrade)."""
    if not isinstance(result, dict):
        return None
    if "fields" in result:
        return result["fields"]
    return result.get("out")


async def _collect_map_outputs(
    wf: WorkflowHandle, step: MapStep, elements: list[tuple[Any, str]]
) -> list[Any]:
    """Gather the collected step's per-element output into an ordered list (#428 §5.2) —
    ``None`` where the element never reached that step (a switch routed away, or it
    failed), so positions stay aligned for a downstream ``map over .outputs``."""
    cname = _collect_name(step)
    collected: list[Any] = []
    for _value, ekey in elements:
        path = _artifact_path(wf, cname, ekey)
        if cname and await wf.exists(path):
            rec = await wf.read_json(path)
            collected.append(_collected_value(rec.get("result") if isinstance(rec, dict) else None))
        else:
            collected.append(None)
    return collected


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
    # plan §2.1: a summary may be a single reference (e.g. ``{steps.classify.outputs}``) so a
    # channel-D decision map can be reviewed at a gate WITHOUT also writing redundant files;
    # otherwise it is a file glob whose matched files are shown.
    if _TOKEN.fullmatch(step.summary_from.strip()):
        return await _resolve(step.summary_from, ns, wf)
    summary: dict[str, Any] = {}
    for p in await wf.glob(await _resolve(step.summary_from, ns, wf)):
        summary[p] = await wf.read_json(p) if p.lower().endswith(".json") else await wf.read_text(p)
    return summary


async def _exec_capability(
    wf: WorkflowHandle, step: CapabilityStep, ns: dict[str, Any], key: str
) -> None:
    if step.call == "ingest_to_collection":
        await wf.ingest_to_collection(
            await _resolve(step.collection, ns, wf),
            await _resolve(step.path, ns, wf),
            phase=step.phase,
        )
    elif step.call == "create_entity":  # #419/#435 — same numbering pipeline, no raw write
        resolved = {
            k: (await _resolve(v, ns, wf) if isinstance(v, str) else v)
            for k, v in step.args.items()
        }
        await wf.create_entity(
            await _resolve(step.type_name, ns, wf),
            resolved,
            name=step.name,
            on_duplicate=step.on_duplicate or "update",
            key=key,  # the map-element scope key: one entity per element (§P2)
            phase=step.phase,
        )
    elif step.call == "update_entity":  # #429 P2 — same EntityStore path, optimistic-retry
        patch = {
            k: (await _resolve(v, ns, wf) if isinstance(v, str) else v)
            for k, v in step.args.items()
        }
        await wf.update_entity(
            await _resolve(step.type_name, ns, wf),
            await _resolve_number(step.number, ns, wf),
            patch,
            phase=step.phase,
        )
    elif step.call == "send_notification":  # #435 P5 — M1 send-once over the notification store
        a = {
            k: (await _resolve(v, ns, wf) if isinstance(v, str) else v)
            for k, v in step.args.items()
        }
        await wf.send_notification(
            a["recipient"],
            a["topic"],
            name=step.name,
            title=a.get("title", ""),
            body=a.get("body", ""),
            window=step.window,  # #435 P8: per-window fingerprint (once-per-period)
            key=key,
            phase=step.phase,
        )
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
        # #429 P4: prune orphan per-element artifacts left by a now-smaller set before
        # re-running (cleanup tied to the moment the set resolves).
        await _gc_map_orphans(wf, step, {ekey for _, ekey in elements})

        async def _one(elem: tuple[Any, str]) -> None:
            value, ekey = elem
            # #428 §1.1: an inner ``{steps.x.f}`` resolves at this element's key.
            sub = {**ns, step.as_: value, "__key__": ekey}
            # #429 P5: run the element's steps on a per-element sub-handle so its agent
            # turn drives its OWN turn lane (real parallel) instead of serializing; the
            # sub-handle shares the workspace + journal, so artifacts land unchanged.
            ewf = wf.sub_handle(ekey)
            for inner in step.do:
                await _exec_step(ewf, inner, sub, ekey, failures)

        failures.extend(await wf.map(_one, elements, concurrency=_map_concurrency(step, wf)))
        if step.name:  # #428 §5: fan-in — publish the collected outputs as this map's field
            collected = await _collect_map_outputs(wf, step, elements)
            await wf.write_json(
                _artifact_path(wf, step.name, key),
                {"hash": "", "result": {"fields": {"outputs": collected}}},
            )
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
        if step.name:  # #428 §6: expose the decision's feedback as {steps.<name>.feedback}
            await wf.write_json(
                _artifact_path(wf, step.name, key),
                {
                    "hash": "",
                    "result": {
                        "fields": {
                            "feedback": decision.input,
                            "choice": decision.choice,
                        }
                    },
                },
            )
        if decision.choice == "approve":
            return
        if decision.choice == "revise" and step.revise_to:
            # Data-driven invalidation: clear only this gate's decision so it re-pauses;
            # the feedback (now journaled above) re-runs revise_to via §9 hash-chaining.
            await wf.delete(_decision_path(wf, step.phase))
            raise _Revise
        raise _Stop(decision.choice)
    if isinstance(step, CapabilityStep):
        await _exec_capability(wf, step, ns, key)
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
            reads=await _resolve_reads(step.reads, ns, wf),
            cache=step.cache,
        )
        return
    # AgentStep — validate_def guarantees exactly one output kind: ``outputs`` (channel D)
    # XOR ``out``+``kind`` (channel P), §2.1. So every agent routes through agent_write_step.
    prompt = await _resolve(step.prompt, ns, wf)
    tools = step.tools or None
    # #428 §1.2: an ``outputs`` step parses its reply into result.fields, gated on it; a
    # prose ``out`` step defaults to artifact_valid(out, kind) (plan §2.2) unless the
    # author gives an explicit ``check``.
    if step.outputs:
        check: Check | None = _outputs_check(step.outputs)
    elif step.check:
        check = await _build_check(step.check, ns, wf)
    else:
        check = None
    await agent_write_step(
        wf,
        prompt=prompt,
        phase=step.phase,
        out=await _resolve(step.out, ns, wf) if step.out else "",
        kind=step.kind or "text",
        requires=step.requires or None,
        tools=tools,
        name=step.name or None,
        key=key,
        retries=step.retries,
        check=check,
        outputs=step.outputs or None,
        reads=await _resolve_reads(step.reads, ns, wf),
        cache=step.cache,
    )


def build_run(d: WorkflowDef):
    """A ``ProfileRun`` (``async def run(wf, inputs)``) that interprets ``d`` over the
    existing step primitives — the orchestrator runs it exactly like a hand-written one."""

    async def run(wf: WorkflowHandle, inputs: Any) -> dict[str, Any]:
        while True:  # #428 §6: a gate `revise` re-drives from the top; else runs once
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
            except _Revise:
                continue  # feedback recorded + decision cleared → replay reruns the target
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


_REQUIRES_KEYS = ("contains", "min_length")


def _validate_requires(requires: dict[str, Any], where: str, errs: list[str]) -> None:
    """Static check of a channel-P ``requires`` contract (plan §2.3 L2): only known keys;
    ``contains`` a list of strings; ``min_length`` a non-negative int."""
    for k in requires:
        if k not in _REQUIRES_KEYS:
            errs.append(f"{where}: unknown 'requires' key {k!r} (one of {list(_REQUIRES_KEYS)})")
    contains = requires.get("contains")
    if contains is not None and not (
        isinstance(contains, list) and all(isinstance(s, str) for s in contains)
    ):
        errs.append(f"{where}: 'requires.contains' must be a list of strings")
    ml = requires.get("min_length")
    if ml is not None and not (isinstance(ml, int) and not isinstance(ml, bool) and ml >= 0):
        errs.append(f"{where}: 'requires.min_length' must be a non-negative integer")


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


def _validate_reads(
    reads: list[str],
    scope: set[str],
    where: str,
    errs: list[str],
    steps_seen: dict[str, dict[str, Any]],
) -> None:
    """Static path-shape check for a step's ``reads`` (#429 P1). A declared read is a
    workspace path/glob; an empty entry or a ``..`` traversal is a static error (a
    malformed dependency should be caught before the run, not silently ignored). The
    interpolation references inside each entry are checked like any other template."""
    for entry in reads:
        # the interpolated skeleton (tokens blanked) must still be a sane path shape
        skeleton = _TOKEN.sub("", entry).strip()
        if not skeleton and not _TOKEN.search(entry):
            errs.append(f"{where}: a 'reads' entry must be a non-empty path")
        elif ".." in entry.split("/"):
            errs.append(f"{where}: a 'reads' entry cannot contain a '..' traversal")
    _check_interp(reads, scope, where, errs, steps_seen)


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
        if step.name:  # #428 §5.1: a named map must have one identifiable collect step
            cname = _collect_name(step)
            if not cname:
                errs.append(
                    f"{where}: named map {step.name!r} has no single output step to collect — "
                    "declare 'outputs' on exactly one inner step or set 'collect'"
                )
            elif cname not in _named_steps_in(step.do):
                errs.append(f"{where}: map 'collect' names unknown step {cname!r}")
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
        # #428 §6: revise bounces to a top-level target, so revise_to only makes sense on
        # a direct top-level gate (depth 0); a gate inside a switch case (depth>0) can't.
        if step.revise_to and depth > 0:
            errs.append(f"{where}: 'revise_to' is only allowed on a top-level gate (§6)")
        _check_interp(step.title, scope, where, errs, steps_seen)
        return
    if isinstance(step, CapabilityStep):
        if step.call not in capabilities:
            errs.append(
                f"{where}: capability {step.call!r} is not allowed (one of {list(capabilities)})"
            )
        else:
            for req in _CAP_REQUIRED.get(step.call, ()):
                if not getattr(step, req):
                    errs.append(f"{where}: capability {step.call!r} needs {req!r}")
            for areq in _CAP_REQUIRED_ARGS.get(step.call, ()):  # #435 P5: args-carried reqs
                if not step.args.get(areq):
                    errs.append(f"{where}: capability {step.call!r} needs {areq!r} in 'args'")
            # #435: a non-idempotent capability (one with a fixed output schema) needs a
            # ``name`` — its stable dedup identity — else two sites would collide.
            if step.call in _CAP_OUTPUTS and not step.name:
                errs.append(
                    f"{where}: capability {step.call!r} needs a 'name' (#435 dedup identity)"
                )
            # #435 决议4: ``on_duplicate`` is validated against THIS capability's policy set.
            # ``create_new`` (M2 token, P7) is now in the set — the per-invocation ``run_id``
            # makes each separate invocation mint fresh, so the old P4 gate is retired.
            else:
                allowed_pol = _CAP_ON_DUPLICATE.get(step.call, ())
                if step.on_duplicate and step.on_duplicate not in allowed_pol:
                    errs.append(
                        f"{where}: capability {step.call!r} 'on_duplicate' must be one of "
                        f"{list(allowed_pol)}"
                        if allowed_pol
                        else f"{where}: capability {step.call!r} does not take an 'on_duplicate'"
                    )
            # #435 P8: ``window`` is validated against send_notification's period set — same
            # per-capability shape as on_duplicate; a capability without a window policy
            # rejects any window.
            allowed_win = _CAP_WINDOW.get(step.call, ())
            if step.window and step.window not in allowed_win:
                errs.append(
                    f"{where}: capability {step.call!r} 'window' must be one of {list(allowed_win)}"
                    if allowed_win
                    else f"{where}: capability {step.call!r} does not take a 'window'"
                )
        _check_interp(
            [step.collection, step.path, step.title, step.body, step.keys, step.args, step.number],
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
        _validate_reads(step.reads, scope, where, errs, steps_seen)
        return
    # AgentStep — plan §2.1 (P2): exactly ONE output kind, so a node is either a
    # structured decision (``outputs``, channel D) OR a prose artifact (``out``+``kind``,
    # channel P), never both and never neither. This closes 'no verify' (a node with no
    # output has no gate) and 'two output kinds in one turn' (unreliable on local models).
    if not step.prompt:
        errs.append(f"{where}: agent needs a 'prompt'")
    if step.out and step.outputs:
        errs.append(
            f"{where}: an agent step declares ONE output kind — 'outputs' (structured "
            "decision) XOR 'out'+'kind' (prose artifact), not both (plan §2.1)"
        )
    elif not step.out and not step.outputs:
        errs.append(
            f"{where}: an agent step must produce an output — declare 'outputs' "
            "(structured decision) or 'out'+'kind' (prose artifact) (plan §2.1)"
        )
    if step.requires:  # plan §2.3 L2: only on a channel-P ``out`` step, folds into its gate
        if not step.out:
            errs.append(f"{where}: 'requires' is only valid on a prose 'out' step (plan §2.3)")
        if step.check is not None:
            errs.append(
                f"{where}: 'requires' folds into the default gate — use 'requires' OR a "
                "custom 'check', not both"
            )
        _validate_requires(step.requires, where, errs)
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
    _validate_reads(step.reads, scope, where, errs, steps_seen)


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


def _references_feedback(step: Step, gatename: str) -> bool:
    """Does ``step``'s prompt / run text reference ``{steps.<gatename>.feedback}`` (#428
    §6)? The revise target must, or the feedback would be threaded nowhere."""
    text = getattr(step, "prompt", "") or getattr(step, "run", "")
    return any(
        m.group(1).strip().split(".") == ["steps", gatename, "feedback"]
        for m in _TOKEN.finditer(text)
    )


def _validate_revise(steps: list[Step], errs: list[str]) -> None:
    """#428 §6: enforce the five constraints that keep a gate's ``revise`` back-edge sound.
    ``revise`` in ``allow`` ⇔ ``revise_to`` is set; the gate is named (its feedback is the
    forward reference); ``revise_to`` is a top-level step declared *before* the gate; the
    target references ``{steps.<gate>.feedback}``; and no other gate sits between them (so
    the re-drive can't skip past an intervening, still-pending decision)."""
    index_by_name: dict[str, int] = {}
    for i, s in enumerate(steps):
        nm = getattr(s, "name", "")
        if nm and nm not in index_by_name:
            index_by_name[nm] = i
    for j, s in enumerate(steps):
        if not isinstance(s, GateStep):
            continue
        where = f"steps[{j}]"
        if ("revise" in s.allow) != bool(s.revise_to):
            errs.append(f"{where}: gate 'revise' in allow and 'revise_to' must be set together")
        if not s.revise_to:
            continue
        if not s.name:
            errs.append(f"{where}: a gate with 'revise_to' needs a 'name' (for its feedback)")
        target_i = index_by_name.get(s.revise_to)
        if target_i is None or target_i >= j:
            errs.append(
                f"{where}: 'revise_to' must name a top-level step before this gate, "
                f"got {s.revise_to!r}"
            )
            continue
        if any(isinstance(steps[k], GateStep) for k in range(target_i + 1, j)):
            errs.append(f"{where}: another gate lies between the 'revise_to' target and this gate")
        if s.name and not _references_feedback(steps[target_i], s.name):
            errs.append(
                f"{where}: the 'revise_to' target {s.revise_to!r} must reference "
                f"{{steps.{s.name}.feedback}}"
            )


def _register_step(
    step: Step, steps_seen: dict[str, dict[str, Any]], where: str, errs: list[str]
) -> None:
    """After a step is validated, record its ``name`` → declared output fields so a later
    sibling can reference it (#428 §1.5). A named map exposes only ``.outputs`` (§5.3).
    Names are unique within a scope."""
    if isinstance(step, GateStep):
        # #428 §6: top-level gates are pre-seeded (their feedback is a forward reference);
        # a nested gate is not referenceable, so neither is registered here.
        return
    name = getattr(step, "name", "")
    if not name:
        return
    if name in steps_seen:
        errs.append(f"{where}: duplicate step name {name!r}")
    elif isinstance(step, MapStep):
        # #428 §5.3: from outside, only the fan-in ``.outputs`` (a list) is referenceable.
        steps_seen[name] = {"outputs": "list"}
    elif isinstance(step, CapabilityStep):
        # #435: a named capability exposes its FIXED (owner-defined) output schema, so a
        # downstream ``{steps.<name>.<field>}`` validates against what it actually produces.
        steps_seen[name] = dict(_CAP_OUTPUTS.get(step.call, {}))
    else:  # a named AgentStep / SandboxStep
        assert isinstance(step, AgentStep | SandboxStep)
        # #428 §1.5/§3.4: keep the whole ``outputs`` declaration so a reference can be
        # checked for field existence AND a switch can check its cases against an enum.
        steps_seen[name] = dict(step.outputs)


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
    seed: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Validate an ordered step list in one naming scope: each step is checked against
    the names declared *before* it (``steps_seen`` accumulates in order), then registers
    its own name (#428 §1.5). ``depth`` bounds nested switches (§3.2). ``seed`` pre-loads
    names visible from the first step — used for the one legal forward reference, a gate's
    feedback (§6)."""
    steps_seen: dict[str, dict[str, Any]] = dict(seed) if seed else {}
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
    # #428 §6: pre-register top-level gates so a revise target's {steps.<gate>.feedback}
    # (the one legal forward reference) resolves during validation; a gate exposes only
    # its feedback + choice.
    gate_seed: dict[str, dict[str, Any]] = {}
    for s in d.steps:
        if isinstance(s, GateStep) and s.name:
            if s.name in gate_seed:
                errs.append(f"steps: duplicate gate name {s.name!r}")
            gate_seed[s.name] = {"feedback": "str", "choice": "str"}
    _validate_steps(
        d.steps,
        "steps",
        declared,
        {"config", "inputs", "steps"},  # #428 §1: the named-output reference namespace
        tool_ceiling,
        capabilities,
        errs,
        top=True,
        seed=gate_seed,
    )
    _validate_revise(d.steps, errs)
    return errs


# ─── authoring reference (machine-derived; plan §3.2 P5/P6) ───────────────────

_STEP_CLASSES = (AgentStep, SandboxStep, GateStep, CapabilityStep, MapStep, SwitchStep)


def _first_sentence(doc: str | None) -> str:
    if not doc:
        return ""
    text = " ".join(doc.split())
    end = text.find(". ")
    return text[: end + 1] if end != -1 else text


def describe_dsl_grammar() -> str:
    """A machine-derived reference for authoring a ``workflow.json`` (plan §3.2, P5).
    Derived from the schema — the step Structs + the capability/check/type registries — so
    it never drifts from what the interpreter actually accepts, unlike a hand-maintained
    list. The ``author-workflow`` skill stays purpose-only and appends this at load time."""
    lines = [
        "## Workflow DSL — machine-derived reference (always current)",
        "",
        'A workflow.json is: {"id", "title", "phases": [{"id","title"}], "config": {…}, '
        '"steps": [ …ordered steps… ]}.',
        "",
        "Fill a value with a read-only lookup (no logic, no expressions): {config.X} (from "
        "config), {inputs.Y} (from the trigger), {item} / {item.field} (the current map "
        "element), or {steps.NAME.FIELD} (a named earlier step's declared output field).",
        "",
        'Each step\'s "type" is one of:',
    ]
    for cls in _STEP_CLASSES:
        tag = cls.__struct_config__.tag
        fields = msgspec.structs.fields(cls)
        req = [f.encode_name for f in fields if f.required]
        opt = [f.encode_name for f in fields if not f.required]
        lines.append(f"- **{tag}** — {_first_sentence(cls.__doc__)}")
        lines.append(f"  - required: {', '.join(req) or '(none)'}")
        lines.append(f"  - optional: {', '.join(opt) or '(none)'}")
    lines += [
        "",
        f"capability `call` ∈ {list(CAPABILITIES)}.",
        f"deterministic `check` ∈ {list(_CHECKS)} (an agent may instead declare `outputs`).",
        f'`outputs` field types ∈ {list(_OUTPUT_TYPES)} (optionally {{"type":…, "enum":[…]}}).',
        f"prose `out` `kind` ∈ {list(ARTIFACT_KINDS)}; `requires` keys ∈ {list(_REQUIRES_KEYS)}.",
        "An agent step declares exactly ONE output kind: `outputs` (structured) XOR "
        "`out`+`kind` (prose).",
    ]
    return "\n".join(lines)


def describe_workflow_boundaries(tool_ceiling: set[str] | None) -> str:
    """What a workflow authored for this app can and cannot do (plan §3.2, P6): the agent-
    step tool ceiling (save_workflow rejects anything outside it) and the available
    capabilities. ``None`` ceiling ⇒ no per-profile clamp is known (all profile tools)."""
    tools = (
        "all of the profile's tools"
        if tool_ceiling is None
        else (", ".join(sorted(tool_ceiling)) or "(none)")
    )
    return "\n".join(
        [
            "## What a workflow here can and cannot do (this app)",
            "",
            f"- an `agent` step's `tools` must be within: {tools} "
            "(save_workflow rejects anything outside — don't guess).",
            f"- reliable side-effects go ONLY through a `capability`: {', '.join(CAPABILITIES)}.",
            "- a `sandbox` step is compute-only (no credential); it cannot reach collections.",
        ]
    )


# ─── stale-cache lint (#429 P1) ──────────────────────────────────────────────

# A cheap, deliberately-heuristic signal that a sandbox command probably reads a file:
# a path separator, a glob metachar, or a ``name.ext`` token. It is NOT an attempt to
# parse the (opaque) command — it only nudges the author to DECLARE ``reads``.
_PATH_LIKE = re.compile(r"/|[*?\[]|\b[\w-]+\.[A-Za-z][\w]*\b")


def _is_glob_over(over: str | dict[str, Any]) -> bool:
    """Does a map's ``over`` expand file PATHS (a glob) rather than a list value? True only
    for a string carrying a glob metachar — a whole ``{ref}`` list value is not flagged."""
    return isinstance(over, str) and any(c in over for c in "*?[")


def _walk_stale(steps: list[Step], *, in_glob_map: bool, warns: list[str]) -> None:
    for s in steps:
        if isinstance(s, SandboxStep) and s.cache and not s.reads:
            what = s.name or s.phase
            if in_glob_map:
                warns.append(
                    f"sandbox step {what!r} inside a map over a glob declares no 'reads' — its "
                    "command won't re-run when a matched file's CONTENT changes; declare 'reads' "
                    "or set 'cache': false"
                )
            elif _PATH_LIKE.search(s.run):
                warns.append(
                    f"sandbox step {what!r} looks like it reads a file but declares no 'reads' — a "
                    "content-only change won't re-run it; declare 'reads' or set 'cache': false "
                    "(heuristic — ignore if the command reads nothing)"
                )
        if isinstance(s, MapStep):
            _walk_stale(s.do, in_glob_map=_is_glob_over(s.over), warns=warns)
        elif isinstance(s, SwitchStep):
            for case in s.cases.values():
                _walk_stale(case, in_glob_map=in_glob_map, warns=warns)
            if s.default:
                _walk_stale(s.default, in_glob_map=in_glob_map, warns=warns)


def stale_risk_warnings(d: WorkflowDef) -> list[str]:
    """Advisory stale-cache warnings for a parsed DSL (#429 P1), as human-readable strings.

    Deliberately conservative and low-noise: it flags only the two statically-detectable
    stale shapes — a sandbox command that *looks* like it reads a file yet declares no
    ``reads`` (and isn't ``cache: false``), and any sandbox step inside a ``map`` over a
    glob without ``reads`` (the highest-risk shape: the glob's members are the varying
    content). It never parses the opaque command to guess what it reads, and it never
    fires on a step that has taken a stance (declared ``reads`` or set ``cache: false``).
    Always advisory — ``workflow check`` surfaces these as warnings, never errors, because
    'declares no reads' is legitimately correct for a step that depends on no file."""
    warns: list[str] = []
    _walk_stale(d.steps, in_glob_map=False, warns=warns)
    return warns
