#!/usr/bin/env python
"""Live canned check (#287): a *scaffolded* workflow actually runs end-to-end.

Scaffolds the `minimal` recipe into a throwaway playground profile, builds the real
app (bundled defaults + the real LitellmAgentRunner pointed at local Ollama), triggers
the run through the real HTTP routes, and asserts it reaches `done` with the agent's
file written. Cleans up the scaffolded profile afterwards.

    uv run python scripts/check_workflow_scaffold_287.py --base-url http://localhost:11434
"""

from __future__ import annotations

import argparse
import shutil
import time
from importlib import resources
from pathlib import Path

from fastapi.testclient import TestClient

from workspace_app.api import create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.config.loader import load
from workspace_app.factories import get_agent_config_catalog, get_runner
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.scaffold import scaffold_workflow

_PROFILE = "lc287"
_WF = "note"


def _poll(client: TestClient, base: str, run_id: str, want: str, tries: int = 1200) -> dict:
    data: dict = {}
    for _ in range(tries):
        data = client.get(f"{base}/runs/{run_id}").json()
        if data["status"] == want:
            return data
        if data["status"] in ("error", "cancelled"):
            raise AssertionError(f"run reached {data['status']}: {data.get('result')}")
        time.sleep(0.25)
    raise AssertionError(f"run never reached {want!r}: last status={data.get('status')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:11434", help="Ollama base url")
    args = ap.parse_args()

    apps_dir = Path(str(resources.files("workspace_app.apps")))
    profile_dir = apps_dir / "playground" / "profiles" / _PROFILE
    try:
        scaffold_workflow(apps_dir, "playground", _PROFILE, _WF, recipe="minimal")
        scaffold_workflow(apps_dir, "playground", _PROFILE, "draft", recipe="review-commit")
        print(f"scaffolded minimal + review-commit into {profile_dir}")

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
            .create(PlaygroundItem(title="lc287", owner="op", profile=_PROFILE))
            .resource_id
        )
        with TestClient(app) as client:
            base = f"/api/a/playground/items/{item}"  # #177: routes live under /api
            client.put(f"{base}/files/uploads/input.json", content=b"{}")

            # minimal → done
            print("== minimal: run to done ==")
            run_id = client.post(f"{base}/run", params={"workflow_id": _WF}).json()["run_id"]
            done = _poll(client, base, run_id, "done")
            assert done["result"]["status"] == "done", done["result"]
            note = client.get(f"{base}/files/note.md")
            assert note.status_code == 200 and note.content.strip(), "note.md was not written"
            print(f"  ✓ done; note.md ({len(note.content)}B): {note.content[:80]!r}")

            # review-commit → produce → awaiting_human → approve → done
            print("== review-commit: produce → gate → approve → commit ==")
            rc = client.post(f"{base}/run", params={"workflow_id": "draft"}).json()["run_id"]
            _poll(client, base, rc, "awaiting_human")
            draft = client.get(f"{base}/files/draft.md")
            assert draft.status_code == 200 and draft.content.strip(), "draft.md not written"
            print(f"  ✓ paused at gate; draft.md ({len(draft.content)}B)")
            client.post(f"{base}/runs/{rc}/decisions", json={"choice": "approve"})
            rc_done = _poll(client, base, rc, "done")
            assert rc_done["result"]["status"] == "approved", rc_done["result"]
            committed = client.get(f"{base}/files/committed.md")
            assert committed.status_code == 200 and committed.content == draft.content
            print(f"  ✓ approved → committed.md matches draft ({len(committed.content)}B)")
        print("\nLIVE CHECK PASSED — scaffolded minimal + review-commit ran against Ollama")
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
        print(f"cleaned up {profile_dir}")


if __name__ == "__main__":
    main()
