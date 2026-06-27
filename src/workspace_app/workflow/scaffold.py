"""Workflow scaffold (#287) — `python -m workspace_app.workflow new` writes a
runnable, heavily-annotated ``run.py`` from a recipe and registers it in the profile's
``_profile.json`` so a developer starts from working code instead of a blank file.

Three recipes, each a real shape the platform uses, each ``check``-clean out of the box
(every ``phase=`` literal it emits is declared in the manifest it ships with):

* ``minimal`` — one ``agent_write_step``; runs to **done** with no setup.
* ``review-commit`` — produce → ``human_gate`` → deterministic commit (the
  decision/action split, manual §8); runs to **awaiting_human** until approved.
* ``batch`` — ``wf.map`` over the upload folder; runs to **done** (a no-op when empty).

The generated ``run.py``'s docstring points at ``docs/workflows-authoring.md`` (the block
catalog), so the scaffold *is* the first half of the guidance and the guide is the rest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# The docstring of each generated run.py carries the workflow's id at this token (a plain
# str.replace, so the template's own ``{...}`` / f-strings are left untouched).
_ID_TOKEN = "__WF_ID__"

_MINIMAL = '''"""__WF_ID__ — a minimal workflow (scaffolded by the workflow `new` command).

Runs end-to-end with no setup: one agent node writes a short note and the step gates on
the file being non-empty. Edit the prompt, add steps, and grow from here.

The blocks you can call inside run() — agent_step / agent_write_step / sandbox_node /
human_gate / wf.map / wf.ingest_to_collection / wf.upsert_context_card + the gates — are
catalogued in docs/workflows-authoring.md. Keep `phase=` a literal that matches a phase
declared in _profile.json, then run `python -m workspace_app.workflow check` to verify.
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_write_step
from workspace_app.workflow.handle import WorkflowHandle


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    # agent_write_step: the model PRODUCES the file's content as its reply (no write_file
    # tool needed — small models emit tool args unreliably); the step writes it to `out`
    # and gates on file_nonempty(out). tools=[] gives the agent NO tools, so it just
    # answers — add read-only tools (e.g. tools=["read_file"]) once it needs to read input.
    await agent_write_step(
        wf,
        phase="note",
        out="note.md",
        prompt=(
            "Write a short, friendly hello note (2-3 sentences) in Markdown. "
            "Output only the note text — no preamble, no explanation."
        ),
        tools=[],
        retries=2,
    )
    return {"status": "done", "wrote": "note.md"}
'''

_REVIEW_COMMIT = '''"""__WF_ID__ — produce → review → commit (the canonical workflow shape).

An agent DRAFTS (produce) → a human approves at a gate (review) → a deterministic node
COMMITS (commit). The agent only ever DECIDES: it writes the draft as a file and never
holds the committing tool, so the irreversible step can't fire without the human
(manual §8 — the decision/action split). Out of the box this runs to `awaiting_human`;
approve the gate to finish.

To file into a KB collection instead, swap the commit body for
`await wf.ingest_to_collection(collection, path)` / `await wf.upsert_context_card(...)`
(both deterministic, journaled, idempotent). See docs/workflows-authoring.md.
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_write_step, human_gate, run_step
from workspace_app.workflow.handle import WorkflowHandle


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    # PRODUCE: the agent drafts the proposal into a file (a decision, not an action).
    # tools=[] keeps it self-contained; give it tools=["read_file"] once it should read
    # an uploaded brief first.
    await agent_write_step(
        wf,
        phase="produce",
        out="draft.md",
        prompt=(
            "Draft a short proposal (a title and 2-3 bullet points) in Markdown. "
            "Output only the proposal — no preamble, no explanation."
        ),
        tools=[],
        retries=2,
    )

    # REVIEW: pause for a human. On first reach the run suspends as `awaiting_human`; the
    # reviewer opens draft.md, edits it in place, then approves (or rejects).
    decision = await human_gate(
        wf,
        phase="review",
        title="Review the draft, then approve",
        summary="Open draft.md, edit if needed, then Approve to commit it.",
        allow=["approve", "reject"],
    )
    if decision.choice == "reject":
        return {"status": "rejected"}

    # COMMIT: the reliable side effect, as a deterministic node. run_step emits the
    # `commit` phase + journals the result (a re-run skips it). The agent never held this.
    async def _commit(_feedback: str | None) -> dict[str, Any]:
        await wf.write("committed.md", await wf.read_text("draft.md"))
        return {"committed": "committed.md"}

    await run_step(wf, name="commit", phase="commit", args={"src": "draft.md"}, execute=_commit)
    return {"status": "approved", "committed": "committed.md"}
'''

_BATCH = '''"""__WF_ID__ — batch: process every uploaded file in parallel with wf.map.

Files dropped in the profile's upload folder (uploads/ by default) are globbed and each
is handled by its own agent node, bounded by a concurrency cap. A failing element is
collected (skip+collect) rather than killing the batch. Runs to done — a no-op when
nothing is uploaded. See docs/workflows-authoring.md.
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_write_step
from workspace_app.workflow.handle import WorkflowHandle


def _slug(path: str) -> str:
    return path.strip("/").replace("/", "_")


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    files = await wf.glob(
        inputs.get("files", [f"{wf.upload_dir}/*"]),
        exclude=[f"{wf.upload_dir}/input.json"],
    )

    async def _one(path: str) -> None:
        await agent_write_step(
            wf,
            phase="process",
            name=f"process_{_slug(path)}",
            out=f"notes/{_slug(path)}.md",
            prompt=f"Read {path} and summarise it in two sentences.",
            tools=["read_file"],
            retries=2,
        )

    failures = await wf.map(_one, files)
    return {"status": "done", "processed": len(files) - len(failures), "failures": failures}
'''


@dataclass(frozen=True)
class _Recipe:
    body: str
    phases: list[dict[str, str]]
    tag: str
    description: str
    hint: str


RECIPES: dict[str, _Recipe] = {
    "minimal": _Recipe(
        body=_MINIMAL,
        phases=[{"id": "note", "title": "Write a note"}],
        tag="single",
        description="A minimal one-step workflow — a starting point.",
        hint="Runs with no inputs.",
    ),
    "review-commit": _Recipe(
        body=_REVIEW_COMMIT,
        phases=[
            {"id": "produce", "title": "Produce draft"},
            {"id": "review", "title": "Review"},
            {"id": "commit", "title": "Commit"},
        ],
        tag="single",
        description="Produce → review → commit, with a human gate.",
        hint="Approve at the review gate to commit.",
    ),
    "batch": _Recipe(
        body=_BATCH,
        phases=[{"id": "process", "title": "Process each file"}],
        tag="batch",
        description="Process every uploaded file in parallel.",
        hint="Drop files into uploads/.",
    ),
}


def _title(workflow_id: str) -> str:
    return workflow_id.replace("-", " ").replace("_", " ").strip().title() or workflow_id


def scaffold_workflow(
    apps_dir: Path,
    slug: str,
    profile: str,
    workflow_id: str,
    *,
    recipe: str = "minimal",
    force: bool = False,
) -> list[Path]:
    """Write a ``<recipe>`` workflow into ``apps_dir/<slug>/profiles/<profile>/`` and
    register it in ``_profile.json``. Returns the files written (run.py + _profile.json).

    Refuses (``ValueError``) to: use an unknown recipe; use an empty / ``/``-bearing id;
    overwrite an existing workflow of the same id unless ``force``; or target a profile
    that uses the legacy singular ``workflow`` block (adding a ``workflows`` list would
    shadow it — convert that profile first)."""
    if recipe not in RECIPES:
        raise ValueError(f"unknown recipe {recipe!r}; choose one of {sorted(RECIPES)}")
    if not workflow_id or "/" in workflow_id:
        raise ValueError(f"workflow id {workflow_id!r} must be non-empty and contain no '/'")
    spec = RECIPES[recipe]

    profile_dir = apps_dir / slug / "profiles" / profile
    profile_json = profile_dir / "_profile.json"
    run_py = profile_dir / "workflows" / workflow_id / "run.py"

    data: dict = json.loads(profile_json.read_text()) if profile_json.exists() else {}
    if "workflow" in data and "workflows" not in data:
        raise ValueError(
            f"profile {slug}/{profile} uses the legacy singular 'workflow' block; the "
            f"scaffold adds to the 'workflows' list (which would shadow it) — convert it first"
        )
    workflows: list[dict] = data.get("workflows", [])
    if any(w.get("id") == workflow_id for w in workflows) and not force:
        raise ValueError(
            f"workflow {workflow_id!r} already exists in {slug}/{profile}; pass force=True "
            f"to overwrite"
        )

    run_py.parent.mkdir(parents=True, exist_ok=True)
    run_py.write_text(spec.body.replace(_ID_TOKEN, workflow_id), encoding="utf-8")

    entry = {
        "id": workflow_id,
        "title": _title(workflow_id),
        "tag": spec.tag,
        "description": spec.description,
        "hint": spec.hint,
        "phases": spec.phases,
    }
    workflows = [w for w in workflows if w.get("id") != workflow_id] + [entry]
    data["workflows"] = workflows
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return [run_py, profile_json]
