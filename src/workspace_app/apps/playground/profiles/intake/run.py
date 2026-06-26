"""Intake workflow (#100, manual §20) — the canonical produce → review → commit.

PRODUCE: an agent node classifies each dropped file into a collection (its decision
recorded as data, gated by ``choice_in`` — the agent never holds the ingest tool).
REVIEW: a ``human_gate`` shows the routing plan; nothing is committed until approved.
COMMIT: a deterministic ``ingest_to_collection`` node files each piece, idempotently.

A human who spots a bad result stops the run, edits ``plan/<f>.json`` (or deletes
``step_classify/<f>.json``) in the file UI, and presses Run — only the affected files
re-run (§9). Nothing reaches a collection until the review gate is approved.
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_step, choice_in, human_gate
from workspace_app.workflow.handle import WorkflowHandle


def _plan_path(f: str) -> str:
    return "plan/" + f.lstrip("/").replace("/", "_") + ".json"


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    allowed = wf.config["collections"]  # pre-defined in the profile, not per-run
    up = wf.upload_dir.rstrip("/")  # #198: the profile's staging folder (default uploads)
    files = await wf.glob(
        inputs.get("files", [f"{up}/*"]),
        exclude=inputs.get("except", [f"{up}/input.json"]),
    )

    # Phase 1 — PRODUCE: classify+digest every file. Safe — only writes plan/<f>.json.
    plan: dict[str, Any] = {}
    for f in files:
        out = _plan_path(f)
        await agent_step(
            wf,
            phase="classify",
            name=f"classify_{f.lstrip('/').replace('/', '_')}",
            prompt=(
                f"Read the file {f}. Choose the single best collection for it from "
                f"{allowed} and write a one-line digest. Then write a JSON object "
                f'{{"collection": <one of {allowed}>, "digest": <text>}} to {out} '
                f"using write_file. Output nothing else."
            ),
            tools=["read_file", "write_file"],
            check=choice_in(out, key="collection", allowed=allowed),
            retries=2,
        )
        plan[f] = await wf.read_json(out)

    # Phase 2 — REVIEW: the human confirms BEFORE anything is committed to KB.
    decision = await human_gate(
        wf,
        phase="review",
        title="Approve filing these into collections?",
        summary={f: plan[f].get("collection") for f in files},
        allow=["approve", "reject"],
    )
    if decision.choice == "reject":
        return {"status": "rejected", "files": len(files)}

    # Phase 3 — COMMIT: deterministic, idempotent. Only runs after approval.
    committed = []
    for f in files:
        await wf.ingest_to_collection(plan[f]["collection"], f, phase="ingest")
        committed.append(f)
    return {"status": "approved", "committed": len(committed)}
