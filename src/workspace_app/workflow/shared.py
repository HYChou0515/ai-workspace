"""Shared (built-in) workflow TEMPLATES — introduced exactly like tool-packages and
shared skills (#520).

A ``SHARED_WORKFLOWS`` registry maps a template name to its source dir, mirroring
``workspace_app.tooling.packages.PACKAGES`` and
``workspace_app.apps.shared_skills.SHARED_SKILLS``. A real deployment replaces the dict
with its own, same as those two. Source lives under ``sample-workflows/`` at the repo
root, alongside ``sample-tools/`` and ``sample-skills/``.

What makes a template different from a skill is what happens to it. A skill is GRANTED to
an App and read in place at prompt-compose time; a template is COPIED into an item's
``.workflows/`` (see ``workspace_store``), so the user owns their copy and edits it
freely, and the original is never consulted again. That is why there is no per-App opt-in
list here: a template only has to be compatible, and compatibility is already decided by
the profile's tool ceiling, which ``validate_def`` checks at copy time. Adding a
``workflows:`` grant to ``app.json`` would be a second, weaker copy of a check the DSL
validator already performs exactly.

Templates are ordinary ``workflow.json`` DSL documents — the same format a user can write
by hand or co-author with ``save_workflow`` — so a template is a starting point, never a
privileged construct.
"""

from __future__ import annotations

from pathlib import Path

from .dsl import DslError, WorkflowDef, build_manifest, parse_def
from .manifest import WorkflowManifest

_REPO = Path(__file__).resolve().parents[3]
SHARED_WORKFLOWS_DIR = _REPO / "sample-workflows"

# {template name → source dir holding workflow.json}. The KEY is authoritative: it is the
# id the copy is saved under, and ``load_shared_workflow`` forces the parsed def's ``id``
# to match so the two can never drift.
SHARED_WORKFLOWS: dict[str, Path] = {
    # #520: the reference "turn an upload into knowledge" flow — VLM-read an image, file
    # it as a searchable document, and anchor a context card to THAT document (#518), so
    # the result is reachable both semantically and by exact term.
    "image-to-knowledge": SHARED_WORKFLOWS_DIR / "image-to-knowledge",
}


def load_shared_workflow(name: str) -> WorkflowDef:
    """A shared template parsed into a ``WorkflowDef``, its ``id`` forced to the registry
    ``name``. Raises ``DslError`` on an unregistered name, a missing workflow.json, or a
    def that won't parse — loudly, because a template that can't load is a button that
    fails in the user's hands."""
    src = SHARED_WORKFLOWS.get(name)
    path = None if src is None else src / "workflow.json"
    if path is None or not path.is_file():
        avail = ", ".join(sorted(SHARED_WORKFLOWS)) or "(none)"
        raise DslError(f"unknown workflow template {name!r}. available: {avail}")
    import msgspec

    return msgspec.structs.replace(parse_def(path.read_bytes()), id=name)


def shared_workflow_metas() -> list[WorkflowManifest]:
    """``(id, title, phases, description, tag, hint)`` for every registered template that
    loads, sorted by id — what a picker renders. A template that won't load is SKIPPED
    here rather than breaking the whole list; the parametrised test over the registry is
    the loud guard that none of them is broken in the first place."""
    out: list[WorkflowManifest] = []
    for name in sorted(SHARED_WORKFLOWS):
        try:
            d = load_shared_workflow(name)
        except DslError:
            continue
        out.append(build_manifest(d))
    return out
