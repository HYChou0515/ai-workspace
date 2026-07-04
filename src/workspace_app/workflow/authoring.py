"""Authoring-time checks (#287) — a *static* coherence pass over a profile's
workflows so an authoring mistake surfaces with an actionable message *before*
startup, instead of as a boot crash (or a wrong progress diagram at run time).

This is purely additive and deliberately **static** (it parses ``run.py`` with
``ast``; it never ``exec``s it): fast enough for a tight edit loop, safe to run on
unfinished code, and the only way to cross-check the declared phase skeleton against
the ``phase=`` literals the code actually emits. The startup
``discovery.validate_workflow_profiles`` stays the authority that *boots* an App —
it ``exec``s ``run.py``, so it still catches import / ``NameError`` failures a static
pass can't. ``check`` runs *on top of* that, not instead of it.

What it reports, per workflow:

* **error** — ``run.py`` missing / unreadable / won't parse / has no ``run()``;
  a list-form workflow with an empty or duplicate ``id``; a phase with an empty ``id``.
* **warning** — a ``phase="literal"`` used in ``run.py`` that isn't declared in the
  manifest (the drift / typo case). A non-literal ``phase=expr`` is skipped (it can't
  be verified statically); a *declared-but-unused* phase is **not** flagged — a
  workflow may legitimately declare a phase it only reaches conditionally (or not at
  all in a trivial fixture).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib import resources

import msgspec

from ..apps.profiles import ProfileManifest, workflow_profiles
from .dsl import DslError, parse_def, stale_risk_warnings, validate_def
from .manifest import WorkflowManifest

_APPS_PKG = "workspace_app.apps"


def _check_dsl(dsl_path, where: str, *, strict: bool = False) -> list[Diagnostic]:
    """Static checks for a DSL workflow (#323, manual §22): the ``workflow.json`` parses
    and ``validate_def`` finds no problems (a bad step type / undeclared phase / a check
    or capability the platform doesn't know / an out-of-scope interpolation).

    ``strict`` (#429 P1, opt-in per project) escalates the advisory stale-cache nudges to
    errors — making 'take a stance' (declare ``reads`` or set ``cache``) mandatory."""
    try:
        d = parse_def(dsl_path.read_bytes())
    except DslError as exc:
        return [
            Diagnostic(
                "error", where, f"workflow.json won't parse: {exc}", "fix the JSON or step types"
            )
        ]
    return [
        *(
            Diagnostic("error", where, f"workflow.json: {msg}", "fix the workflow definition")
            for msg in validate_def(d)
        ),
        # #429 P1: stale-cache nudges — advisory warnings by default (a step that reads no
        # file legitimately declares no ``reads``); ``strict`` makes them errors.
        *(
            Diagnostic(
                "error" if strict else "warning",
                where,
                f"stale-cache risk: {msg}",
                "declare 'reads' or set 'cache': false",
            )
            for msg in stale_risk_warnings(d)
        ),
    ]


# A profile dir we can read ``_profile.json`` + ``run.py`` from. Both ``pathlib.Path``
# (tests, the scaffold's target tree) and an ``importlib.resources`` traversable (the
# installed package) support ``/``, ``read_bytes`` and ``read_text`` — so ``check`` works
# the same on a temp fixture and on a shipped App.


@dataclass(frozen=True)
class Diagnostic:
    """One authoring problem: its ``level`` (``"error"`` fails ``check`` / would fail
    boot; ``"warning"`` is advisory), ``where`` it is (``app/profile[/workflow]``), the
    ``message``, and an actionable ``hint``."""

    level: str
    where: str
    message: str
    hint: str = ""

    def render(self) -> str:
        line = f"{self.level}: {self.where}: {self.message}"
        return f"{line}\n    hint: {self.hint}" if self.hint else line


def _phase_literals(tree: ast.AST) -> set[str]:
    """Every string ``phase="..."`` keyword argument anywhere in ``run.py``. Non-literal
    ``phase=expr`` (an f-string, a variable, a helper call) is skipped — it can't be
    cross-checked, and the convention is that ``phase`` is a literal (the dynamic part of
    a step's identity rides ``name=`` / ``key=``)."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "phase"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    out.add(kw.value.value)
    return out


def _defines_run(tree: ast.Module) -> bool:
    """``run.py`` exposes a top-level ``run`` (the orchestration entry, manual §3)."""
    return any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "run"
        for node in tree.body
    )


def _check_workflow(
    run_dir, manifest: WorkflowManifest, where: str, *, strict: bool = False
) -> list[Diagnostic]:
    """Static checks for one workflow: its ``run.py`` loads + defines ``run()``, its
    phase ids are non-empty, and every ``phase=`` literal it emits is declared."""
    diags: list[Diagnostic] = []
    for phase in manifest.phases:
        if not phase.id:
            diags.append(
                Diagnostic(
                    "error",
                    where,
                    "a declared phase is missing its 'id'",
                    "every phase in _profile.json needs a stable, non-empty id",
                )
            )

    # #323: a workflow authored as DATA — a ``workflow.json`` (manual §22) — is validated
    # against the DSL schema + rules instead of parsing a ``run.py``. The interpreter runs
    # it (discovery.load_run_callable), so its loud guard is here, not a Python parse.
    dsl_path = run_dir / "workflow.json"
    if dsl_path.is_file():
        return [*diags, *_check_dsl(dsl_path, where, strict=strict)]

    try:
        text = (run_dir / "run.py").read_text(encoding="utf-8")
    except OSError:
        return [
            *diags,
            Diagnostic(
                "error",
                where,
                "run.py is missing or unreadable",
                f"create {run_dir}/run.py with `async def run(wf, inputs): ...`",
            ),
        ]
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [
            *diags,
            Diagnostic(
                "error",
                where,
                f"run.py has a syntax error: {exc.msg} (line {exc.lineno})",
                "check can't validate a file that won't parse — fix the syntax first",
            ),
        ]

    if not _defines_run(tree):
        diags.append(
            Diagnostic(
                "error",
                where,
                "run.py has no run() function",
                "define `async def run(wf, inputs): ...` — the orchestration entry (manual §3)",
            )
        )

    declared = {phase.id for phase in manifest.phases}
    for literal in sorted(_phase_literals(tree) - declared):
        diags.append(
            Diagnostic(
                "warning",
                where,
                f"phase {literal!r} is used in run.py but not declared in _profile.json",
                f'add {{"id": "{literal}"}} to this workflow\'s phases (or fix the typo)',
            )
        )
    return diags


def _check_workflow_ids(workflows: list[WorkflowManifest], where: str) -> list[Diagnostic]:
    """List-form ids must be non-empty + unique — a workflow is addressed by its id."""
    diags: list[Diagnostic] = []
    seen: set[str] = set()
    for wf in workflows:
        if not wf.id:
            diags.append(
                Diagnostic(
                    "error",
                    where,
                    "a workflow in 'workflows' is missing its 'id'",
                    "each entry needs a stable, non-empty id (its run.py lives at "
                    "workflows/<id>/run.py)",
                )
            )
        elif wf.id in seen:
            diags.append(
                Diagnostic(
                    "error",
                    where,
                    f"duplicate workflow id {wf.id!r}",
                    "ids must be unique within a profile",
                )
            )
        else:
            seen.add(wf.id)
    return diags


def check_profile_dir(profile_dir, where: str, *, strict: bool = False) -> list[Diagnostic]:
    """Every authoring problem in one profile's workflows (``_profile.json`` + each
    ``run.py``), as a flat diagnostics list — empty when the profile is clean. ``where``
    is the human label (e.g. ``"playground/intake"``). Reads files through ``profile_dir``
    (a ``Path`` or a resources traversable), so it works on a temp fixture and a shipped
    App alike. ``strict`` (#429 P1) escalates stale-cache warnings to errors."""
    pm = msgspec.json.decode((profile_dir / "_profile.json").read_bytes(), type=ProfileManifest)
    if pm.workflows:
        diags = _check_workflow_ids(pm.workflows, where)
        for wf in pm.workflows:
            sub = f"{where}/{wf.id}" if wf.id else where
            diags += _check_workflow(profile_dir / "workflows" / wf.id, wf, sub, strict=strict)
        return diags
    if pm.workflow is not None:  # legacy singular form — run.py at the profile root
        return _check_workflow(profile_dir, pm.workflow, where, strict=strict)
    return []


def check_app(slug: str, *, strict: bool = False) -> list[Diagnostic]:
    """Every authoring problem across all of an App's workflow profiles — the flat
    list ``check`` prints and the CI gate asserts empty. An App with no workflows
    (only interactive profiles) yields ``[]``. ``strict`` (#429 P1) escalates stale-cache
    warnings to errors."""
    apps = resources.files(_APPS_PKG)
    diags: list[Diagnostic] = []
    for name in workflow_profiles(slug):
        diags += check_profile_dir(apps / slug / "profiles" / name, f"{slug}/{name}", strict=strict)
    return diags
