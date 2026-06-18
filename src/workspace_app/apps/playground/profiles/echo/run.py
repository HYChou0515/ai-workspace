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
        # credential injection. No gate (a command is often its own check).
        await sandbox_node(wf, phase="think", run="echo hello")
    if inputs.get("ingest"):
        # Reliable side-effect (manual §8): write → ingest → verify it landed.
        cid = inputs["ingest"]
        await wf.write("out/doc.md", "stable document content for ingest")
        await wf.ingest_to_collection(cid, "out/doc.md", phase="think")
        verdict = await collection_has(cid, "out/doc.md")(wf, None)
        return {"status": "done", "n": n, "landed": verdict.ok}
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
