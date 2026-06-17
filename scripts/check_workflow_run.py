#!/usr/bin/env python
"""Live canned check (#100, manual §20 / plan P13): the full produce → review →
commit workflow path against a real LLM (local Ollama).

Drives the REAL ``LitellmAgentRunner`` (the same agent loop a live turn uses) over
the shipped ``playground/intake`` workflow profile, through the real HTTP routes:

  trigger → classify (agent writes plan/<f>.json, gated) → awaiting_human
          → approve → ingest → poll done                         (commit happens)
  trigger → ... → awaiting_human → reject → done                 (nothing committed)

No tools beyond read_file/write_file are needed (the agent never holds the ingest
tool — that's a deterministic node), so a MockSandbox is enough; the file ops route
through the FileStore snapshot.

Usage (needs a reachable Ollama):
    uv run python scripts/check_workflow_run.py --base-url http://localhost:11434
"""

from __future__ import annotations

import argparse
import time

from fastapi.testclient import TestClient

from workspace_app.api import create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.config.loader import load
from workspace_app.factories import get_agent_config_catalog, get_runner
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, SourceDoc
from workspace_app.sandbox.mock import MockSandbox


def _poll(client: TestClient, base: str, run_id: str, want: str, tries: int = 600) -> dict:
    data: dict = {}
    for _ in range(tries):
        data = client.get(f"{base}/runs/{run_id}").json()
        if data["status"] == want:
            return data
        if data["status"] in ("error", "done", "cancelled") and data["status"] != want:
            raise AssertionError(f"run reached {data['status']} (wanted {want}): {data['result']}")
        time.sleep(0.25)
    raise AssertionError(f"run never reached {want!r}: last status={data.get('status')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:11434", help="Ollama base url")
    args = ap.parse_args()

    settings = load(config_path=None, env={})
    spec = make_spec()
    runner = get_runner(settings)
    # Point the runner's default endpoint at the requested Ollama (bundled default
    # may already; this makes --base-url authoritative).
    runner.base_url = args.base_url  # ty: ignore[unresolved-attribute]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
        agent_config_catalog=get_agent_config_catalog(settings),
    )

    coll_rm = spec.get_resource_manager(Collection)
    coll_rm.create(Collection(name="kb-docs"))
    coll_rm.create(Collection(name="kb-logs"))

    def _new_item() -> str:
        return (
            spec.get_resource_manager(PlaygroundItem)
            .create(PlaygroundItem(title="intake", owner="op", profile="intake"))
            .resource_id
        )

    def _docs() -> int:
        from specstar import QB

        return len(
            list(
                spec.get_resource_manager(SourceDoc).list_resources(QB.all())  # ty: ignore[invalid-argument-type]
            )
        )

    with TestClient(app) as client:
        # ── APPROVE path: produce → review → commit ──────────────────
        item = _new_item()
        base = f"/a/playground/items/{item}"
        client.put(
            f"{base}/files/inputs/server.log",
            content=b"2026-06-17 ERROR db: connection pool exhausted on host pg-3\n",
        )
        client.put(f"{base}/files/inputs/input.json", content=b"{}")

        print("== APPROVE path ==")
        run_id = client.post(f"{base}/run").json()["run_id"]
        print(f"  run_id: {run_id}")
        gated = _poll(client, base, run_id, "awaiting_human")
        print(f"  routing plan (agent's decision): {gated['pending_decision']['summary']}")
        before = _docs()
        client.post(f"{base}/runs/{run_id}/decisions", json={"choice": "approve"})
        done = _poll(client, base, run_id, "done")
        print(f"  result: {done['result']}")
        assert done["result"]["status"] == "approved", done["result"]
        assert _docs() > before, "approve committed nothing — ingest did not land a doc"
        print("  ✓ classify → review → approve → ingested a doc")

        # ── REJECT path: nothing committed ───────────────────────────
        item2 = _new_item()
        base2 = f"/a/playground/items/{item2}"
        client.put(
            f"{base2}/files/inputs/notes.txt", content=b"design note: prefer idempotent writes\n"
        )
        client.put(f"{base2}/files/inputs/input.json", content=b"{}")

        print("\n== REJECT path ==")
        run2 = client.post(f"{base2}/run").json()["run_id"]
        _poll(client, base2, run2, "awaiting_human")
        before2 = _docs()
        client.post(f"{base2}/runs/{run2}/decisions", json={"choice": "reject"})
        done2 = _poll(client, base2, run2, "done")
        print(f"  result: {done2['result']}")
        assert done2["result"]["status"] == "rejected", done2["result"]
        assert _docs() == before2, "reject still committed a doc — the gate sat after the commit?!"
        print("  ✓ classify → review → reject → nothing committed")

    print("\nLIVE CHECK PASSED")


if __name__ == "__main__":
    main()
