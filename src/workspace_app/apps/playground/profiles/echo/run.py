"""Echo workflow (#100) — a minimal produce → (review) run for the run-engine
integration tests.

It drives a *real* agent turn (scripted in tests) but gates the agent node on a
file the run writes **deterministically**, so the happy path is green without the
scripted agent having to write files. ``inputs`` steer the branches:

- ``check_path`` — point the gate at a missing file to exercise the failing-step path.
- ``retries`` — the agent node's retry budget.
- ``gate`` — when truthy, suspend at a ``human_gate`` (produce → review → commit).
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import (
    agent_step,
    collection_has,
    file_nonempty,
    human_gate,
    sandbox_node,
)
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.preflight import PreflightItem, PreflightReport, Severity


async def preflight(wf: WorkflowHandle, inputs: dict[str, Any]) -> PreflightReport:
    """#283 pre-flight: describe what the run will do + verify its preconditions so the
    launch dialog can confirm before triggering. The required ``n`` check exercises the
    block-on-missing-precondition path; the advisory staged-files check exercises the
    warn-but-allow path."""
    n = inputs.get("n")
    staged = await wf.glob([f"{wf.upload_dir}/*"], exclude=[f"{wf.upload_dir}/input.json"])
    return PreflightReport(
        summary=f"Acknowledge note n={n} and write out/note.json.",
        checks=[
            PreflightItem(
                label="An 'n' value is set in input.json",
                ok=n is not None,
                reason="" if n is not None else 'set "n" in uploads/input.json',
            ),
            PreflightItem(
                label="Files staged in uploads/",
                ok=bool(staged),
                severity=Severity.ADVISORY,
                reason="" if staged else "drop files into uploads/ (optional for echo)",
            ),
        ],
    )


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    n = inputs.get("n", 1)
    # Deterministic node: write a note the agent node's gate can verify.
    await wf.write_json("out/note.json", {"n": n})
    # Agent node: a real turn on the item (scripted in tests). Its gate reads a
    # file the run controls, so it passes regardless of the (scripted) agent.
    await agent_step(
        wf,
        phase="think",
        prompt=f"Acknowledge note n={n}.",
        tools=["read_file"],
        check=file_nonempty(inputs.get("check_path", "out/note.json")),
        retries=inputs.get("retries", 0),
    )
    if inputs.get("sandbox"):
        # Deterministic node in the sandbox (manual §5.2) — exercises the run-scoped
        # credential injection. Gated by default on exit_code == 0 (plan §2.2).
        await sandbox_node(wf, phase="think", run="echo hello")
    if inputs.get("ingest"):
        # Reliable side-effect (manual §8): write → ingest → verify it landed.
        cid = inputs["ingest"]
        await wf.write("out/doc.md", "stable document content for ingest")
        await wf.ingest_to_collection(cid, "out/doc.md", phase="think")
        verdict = await collection_has(cid, "out/doc.md")(wf, None)
        return {"status": "done", "n": n, "landed": verdict.ok}
    if inputs.get("find_card"):
        # #205: exercise the read-only find-overwrite-target capability through the real
        # wiring — the existing card a commit-time upsert(keys) would overwrite (a hit),
        # and a key that matches nothing (a miss → None).
        cid = inputs["find_card"]
        hit = await wf.find_overwrite_card(
            cid, inputs.get("keys", []), title=inputs.get("title", "")
        )
        miss = await wf.find_overwrite_card(cid, ["definitely-absent-key"], title="")
        return {"status": "done", "found": hit, "miss": miss}
    if inputs.get("gate"):
        decision = await human_gate(
            wf,
            phase="review",
            title="Approve the echo?",
            summary={"n": n},
            allow=["approve", "reject"],
        )
        if decision.choice == "reject":
            return {"status": "rejected", "n": n}
        return {"status": "approved", "n": n, "note": decision.input}
    return {"status": "done", "n": n}
