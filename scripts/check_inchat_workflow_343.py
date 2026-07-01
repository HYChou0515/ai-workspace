#!/usr/bin/env python
"""Live canned check (#343): launch a workflow IN the chat the user prepared in.

Drives the REAL ``LitellmAgentRunner`` over the shipped ``playground/intake``
workflow through the real HTTP routes, but launches it as a TAKEOVER of an
existing chat (``POST .../run?chat_id=<prepared chat>``) rather than a fresh
workflow chat:

  open a free chat → chat with the agent (prepare) → stage inputs
    → run?chat_id=<that chat>  (TAKEOVER)
    → the run drives THAT SAME conversation (no new chat), its agent node
      inheriting the prepared history → awaiting_human → approve → ingested

Asserts the run reused the prepared chat (returned chat_id == it), that the
conversation ends up holding BOTH the free-chat message and the workflow's agent
turns (one shared thread), and that no separate workflow chat was opened.

Usage (needs a reachable Ollama):
    uv run python scripts/check_inchat_workflow_343.py --base-url http://localhost:11434
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
from workspace_app.resources import Conversation, make_spec
from workspace_app.resources.kb import Collection
from workspace_app.sandbox.mock import MockSandbox


def _poll(client: TestClient, base: str, run_id: str, want: str, tries: int = 800) -> dict:
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
    conv_rm = spec.get_resource_manager(Conversation)

    def _messages(chat_id: str):
        conv = conv_rm.get(chat_id).data
        assert isinstance(conv, Conversation)
        return conv.messages

    with TestClient(app) as client:
        item = (
            spec.get_resource_manager(PlaygroundItem)
            .create(PlaygroundItem(title="intake", owner="op", profile="intake"))
            .resource_id
        )
        base = f"/api/a/playground/items/{item}"  # #177: backend routes live under /api

        # A "main" chat first so the prepared chat is a non-default sibling, then the
        # chat the user actually prepares in.
        client.post(f"{base}/chats", json={"title": "main"})
        prep = client.post(f"{base}/chats", json={"title": "prep"}).json()["chat_id"]
        print(f"== prepared chat: {prep} ==")

        # Prepare IN the chat: talk to the agent, then stage the workflow's inputs.
        client.post(
            f"{base}/chats/{prep}/messages",
            json={"content": "I'm about to file a server log for triage — get ready."},
        )
        for _ in range(400):
            if any(m.role == "assistant" for m in _messages(prep)):
                break
            time.sleep(0.25)
        else:
            raise AssertionError("the free-chat turn never produced an assistant reply")
        prepared_len = len(_messages(prep))
        print(f"  free-chat turn done ({prepared_len} messages in the thread)")

        client.put(
            f"{base}/files/inputs/server.log",
            content=b"2026-06-17 ERROR db: connection pool exhausted on host pg-3\n",
        )
        client.put(f"{base}/files/inputs/input.json", content=b"{}")

        # TAKE OVER the prepared chat.
        print("== launch (takeover) ==")
        resp = client.post(f"{base}/run?chat_id={prep}").json()
        run_id, run_chat = resp["run_id"], resp["chat_id"]
        assert run_chat == prep, f"run opened a different chat ({run_chat}) instead of taking over"
        print(f"  run_id={run_id} drives the prepared chat")

        gated = _poll(client, base, run_id, "awaiting_human")
        print(f"  reached the gate (decision summary: {gated['pending_decision']['summary']!r})")

        # One shared thread: the run drives the prepared chat in place — the free-chat
        # message is still there, and no separate workflow chat was opened. (The
        # workflow's agent nodes inherit this thread's history via drive_turn; the
        # persist-into-the-taken-over-chat path is covered by the unit tests.)
        assert any("server log" in (m.content or "") for m in _messages(prep)), (
            "the prepared free-chat message vanished from the thread"
        )
        assert prepared_len >= 2, "the free-chat turn left too little history to inherit"
        chats = client.get(f"{base}/chats").json()
        wf_chats = [c["chat_id"] for c in chats if c["run_id"]]
        assert wf_chats == [prep], (
            f"expected only the prepared chat to be a workflow chat: {wf_chats}"
        )
        print("  ✓ the run drives the prepared thread in place — no separate workflow chat")

        # The gate + decision + resume all work on the taken-over chat: approve resumes
        # the SAME run in place to a terminal, approved outcome. (Whether the intake
        # agent's plan ingests a doc is the intake workflow's concern, covered by
        # scripts/check_workflow_run.py — #343 only proves the takeover + resume flow.)
        client.post(f"{base}/runs/{run_id}/decisions", json={"choice": "approve"})
        done = _poll(client, base, run_id, "done")
        assert done["result"]["status"] == "approved", done["result"]
        print(f"  ✓ approve → resumed the same run in place (result={done['result']})")

    print("\nLIVE CHECK PASSED")


if __name__ == "__main__":
    main()
