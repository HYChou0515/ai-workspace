#!/usr/bin/env python
"""Live canned check (#288, manual §10): conversational steer + incremental resume
against a real LLM (local Ollama).

Drives the REAL ``LitellmAgentRunner`` (the same agent loop a live turn uses) over the
shipped ``playground/echo`` workflow profile, through the real HTTP routes:

  trigger {n:7} → done                                  (the original run)
  steer "set n to 42 and redo think" → the read-only steerer proposes a SteerPlan
  approve → apply (rewrite uploads/input.json, invalidate think) → resume → done

The point is to prove the *LLM-dependent* half works with a small local model: that a
real model returns a parseable plan (the steerer's structured output), and that
approving it applies the edits and resumes the SAME run incrementally. A MockSandbox is
enough (the steerer is read-only; file ops route through the FileStore snapshot).

Usage (needs a reachable Ollama):
    uv run python scripts/check_workflow_steer.py --base-url http://localhost:11434
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
from workspace_app.sandbox.mock import MockSandbox


def _poll(client: TestClient, base: str, run_id: str, want: str, tries: int = 600) -> dict:
    data: dict = {}
    for _ in range(tries):
        data = client.get(f"{base}/runs/{run_id}").json()
        if data["status"] == want:
            return data
        time.sleep(0.25)
    raise AssertionError(f"run never reached {want!r}: last status={data.get('status')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:11434", help="Ollama base url")
    args = ap.parse_args()

    settings = load(config_path=None, env={})
    spec = make_spec()
    runner = get_runner(settings)
    runner.base_url = args.base_url  # ty: ignore[unresolved-attribute]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
        agent_config_catalog=get_agent_config_catalog(settings),
    )

    item = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="steer", owner="op", profile="echo"))
        .resource_id
    )
    # Backend routes live under /api (#177); the plain fastapi TestClient (unlike the
    # test harness) does no auto-prefixing, so name it explicitly.
    base = f"/api/a/playground/items/{item}"

    with TestClient(app) as client:
        client.put(f"{base}/files/uploads/input.json", content=b'{"n": 7}')

        print("== original run ==")
        run_id = client.post(f"{base}/run").json()["run_id"]
        print(f"  run_id: {run_id}")
        done = _poll(client, base, run_id, "done")
        assert done["result"]["n"] == 7, done["result"]
        print(f"  result: {done['result']}")

        print("\n== steer (real steerer proposes a plan) ==")
        instruction = (
            "Change uploads/input.json so that n is 42, and invalidate the 'think' "
            "step so it re-runs."
        )
        r = client.post(f"{base}/runs/{run_id}/steer", json={"instruction": instruction})
        assert r.status_code == 202, r.text
        paused = _poll(client, base, run_id, "awaiting_human")
        plan = paused["pending_steer"]
        assert plan is not None, "the steerer produced no plan"
        print(f"  rationale: {plan['rationale']}")
        print(f"  input_edits: {[e['path'] for e in plan['input_edits']]}")
        print(f"  invalidate: {plan['invalidate']}")
        assert plan["input_edits"] or plan["invalidate"], "the steer plan was a no-op"

        print("\n== approve → apply + resume ==")
        r = client.post(f"{base}/runs/{run_id}/steer/confirm", json={"approve": True})
        assert r.status_code == 202, r.text
        resumed = _poll(client, base, run_id, "done")
        print(f"  result: {resumed['result']}")
        # The input file actually changed (the steerer's edit landed + the run re-read it).
        new_input = client.get(f"{base}/files/uploads/input.json").content
        print(f"  uploads/input.json now: {new_input!r}")
        assert resumed["pending_steer"] is None
        if resumed["result"].get("n") == 42:
            print("  ✓ the steer changed the input (n=42) and the run resumed incrementally")
        else:
            # A small model may phrase the edit differently; still require it DID something.
            assert new_input != b'{"n": 7}' or plan["invalidate"], (
                "the steer applied nothing observable"
            )
            print("  ✓ the steer applied + resumed (model varied the exact edit)")

    print("\nLIVE CHECK PASSED")


if __name__ == "__main__":
    main()
