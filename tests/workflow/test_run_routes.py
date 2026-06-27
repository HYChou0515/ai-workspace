"""Workflow HTTP routes (#100, manual §14) — POST run / GET runs / poll / stream /
cancel / decisions, driven end-to-end through the real ChatTurnEngine with a
scripted runner against the `playground/echo` workflow profile.

The run is a background task, so these use ``with TestClient(app) as client:`` — that
keeps a persistent event-loop thread alive so the run progresses while the test
thread polls (a bare ``TestClient`` tears its loop down between requests)."""

import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from specstar import SpecStar

from tests.api._client import TestClient
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.resources.kb import Collection
from workspace_app.sandbox.mock import MockSandbox


def _app(profile: str = "echo") -> tuple[FastAPI, SpecStar, str]:
    spec = make_spec()
    runner = ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()])
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile=profile))
        .resource_id
    )
    return app, spec, item_id


def _route(app: FastAPI, path: str):
    # backend routes now live under /api (#177); accept the bare path callers pass
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) in (path, "/api" + path))  # ty: ignore


def _base(item_id: str) -> str:
    return f"/a/playground/items/{item_id}"


def _put_input(client: TestClient, item_id: str, payload: str) -> None:
    # #198: the workflow's input.json now lives in the profile's upload_dir (uploads/).
    r = client.put(f"{_base(item_id)}/files/uploads/input.json", content=payload)
    assert r.status_code == 204


def _poll(client: TestClient, item_id: str, run_id: str, want: str, tries: int = 200) -> dict:
    data: dict = {}
    for _ in range(tries):
        r = client.get(f"{_base(item_id)}/runs/{run_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] == want:
            return data
        time.sleep(0.02)
    raise AssertionError(f"run never reached {want!r}: last={data}")


def test_profiles_endpoint_flags_workflow():
    app, _spec, _iid = _app()
    with TestClient(app) as client:
        profiles = {p["name"]: p for p in client.get("/a/playground/profiles").json()}
    assert profiles["echo"]["has_workflow"] is True
    assert profiles["echo"]["workflow"]["phases"][0]["id"] == "think"
    assert profiles["default"]["has_workflow"] is False


def test_profiles_endpoint_lists_each_profiles_workflows():
    """Phase 5 (manual §4): a profile may offer several workflows; /profiles returns
    each profile's `workflows` list, with a stable id + manifest per workflow."""
    app, _spec, _iid = _app()
    with TestClient(app) as client:
        profiles = {p["name"]: p for p in client.get("/a/playground/profiles").json()}
    # The list-form `multi` profile surfaces both workflows with their ids + phases.
    multi = profiles["multi"]
    assert multi["has_workflow"] is True
    assert [w["id"] for w in multi["workflows"]] == ["alpha", "beta"]
    assert multi["workflows"][1]["phases"][0]["id"] == "plan"
    # A legacy singular profile still reports one workflow in the list (back-compat).
    assert [w["id"] for w in profiles["echo"]["workflows"]] == [""]
    # An interactive profile has an empty list.
    assert profiles["default"]["workflows"] == []


def test_unknown_app_profiles_404():
    app, _spec, _iid = _app()
    with TestClient(app) as client:
        assert client.get("/a/nope/profiles").status_code == 404


def test_run_to_done_with_result():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 7}')
        r = client.post(f"{_base(item_id)}/run")
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
    assert data["result"] == {"status": "done", "n": 7}
    think = next(p for p in data["phases"] if p["phase"] == "think")
    assert think["status"] == "passed"


def test_run_accepts_uploaded_input_file_then_runs():
    """#197: an external trigger uploads the workflow's input FILES in the same
    multipart POST (we talk to workflows through the workspace). Each part lands at
    its filename'd path, then the run starts and reads them — here the uploaded
    ``uploads/input.json`` steers ``n`` so the result proves the file was read."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/run",
            files={"file": ("uploads/input.json", b'{"n": 9}', "application/json")},
        )
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
        assert client.get(f"{_base(item_id)}/files/uploads/input.json").content == b'{"n": 9}'
    assert data["result"] == {"status": "done", "n": 9}


def test_run_rejects_an_upload_that_escapes_the_workspace():
    """#197: an uploaded path that escapes the workspace root is rejected (400) and NO
    run starts — the whole trigger aborts before anything is written."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(f"{_base(item_id)}/run", files={"file": ("../escape.txt", b"x")})
        assert r.status_code == 400
        assert client.get(f"{_base(item_id)}/runs").json() == []  # nothing started


def test_run_reads_workflow_id_from_a_form_field():
    """#197: a multipart trigger may name the workflow as a `workflow_id` FORM field (not
    only the query param), so an external upload+trigger is one self-contained body."""
    app, _spec, item_id = _app(profile="multi")
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/run",
            data={"workflow_id": "alpha"},
            files={"file": ("note.txt", b"hi")},
        )
        run_id = r.json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
        assert client.get(f"{_base(item_id)}/files/note.txt").content == b"hi"
    assert data["workflow_id"] == "alpha"


def test_run_query_workflow_id_wins_and_a_stray_file_field_is_ignored():
    """#197: the query `workflow_id` overrides a form one, and a non-file value sent
    under the `file` field is skipped — only real uploads are written."""
    app, _spec, item_id = _app(profile="multi")
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/run?workflow_id=alpha",
            data={"workflow_id": "beta", "file": "stray"},
            files={"file": ("real.txt", b"hi")},
        )
        run_id = r.json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
        assert client.get(f"{_base(item_id)}/files/real.txt").content == b"hi"
    assert data["workflow_id"] == "alpha"  # query beat the form's "beta"


def test_failing_step_reports_error_phase_and_reason():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"check_path": "out/missing.json"}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "error")
    assert "missing.json" in data["result"]["error"]
    think = next(p for p in data["phases"] if p["phase"] == "think")
    assert think["status"] == "failed"


def test_run_requires_workflow_profile():
    """A non-workflow profile (playground/default) can't be run headlessly."""
    app, _spec, item_id = _app(profile="default")
    with TestClient(app) as client:
        assert client.post(f"{_base(item_id)}/run").status_code == 422


# ── #283: pre-flight preview (launch dialog) ─────────────────────────────


def test_preview_describes_and_allows_when_preconditions_met():
    """The pre-flight preview returns the workflow's title/phases + the author's
    summary + checklist, and allows 'Run' when every required check passes."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 7}')
        r = client.get(f"{_base(item_id)}/runs/preview")
        assert r.status_code == 200
        body = r.json()
    assert body["can_run"] is True
    assert body["has_preflight"] is True
    assert "out/note.json" in body["summary"]
    assert body["title"]  # the manifest title, for the dialog header
    assert any(p["id"] == "think" for p in body["phases"])
    n_check = next(c for c in body["checks"] if "n" in c["label"])
    assert n_check["ok"] is True and n_check["severity"] == "required"


def test_preview_blocks_when_required_check_fails():
    """A missing required precondition (no ``n`` in input.json) makes can_run False and
    surfaces the fix-it reason — so the dialog can disable 'Run' and explain why."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, "{}")
        body = client.get(f"{_base(item_id)}/runs/preview").json()
    assert body["can_run"] is False
    n_check = next(c for c in body["checks"] if "n" in c["label"])
    assert n_check["ok"] is False and "input.json" in n_check["reason"]


def test_preview_without_preflight_falls_back_to_phases():
    """A workflow with no ``preflight`` still previews: phases only, no checks, runnable."""
    app, _spec, item_id = _app(profile="multi")
    with TestClient(app) as client:
        body = client.get(f"{_base(item_id)}/runs/preview?workflow_id=beta").json()
    assert body["has_preflight"] is False
    assert body["can_run"] is True
    assert body["checks"] == []
    assert [p["id"] for p in body["phases"]][0] == "plan"


def test_preview_advisory_check_passes_when_files_are_staged():
    """With a real file staged in uploads/, echo's advisory staged-files check is ok and
    the run is allowed (the advisory never blocks)."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 1}')
        assert (
            client.put(f"{_base(item_id)}/files/uploads/doc.txt", content=b"hi").status_code == 204
        )
        body = client.get(f"{_base(item_id)}/runs/preview").json()
    assert body["can_run"] is True
    staged = next(c for c in body["checks"] if "uploads" in c["label"])
    assert staged["ok"] is True and staged["severity"] == "advisory"


def test_preview_unknown_workflow_422():
    app, _spec, item_id = _app(profile="multi")
    with TestClient(app) as client:
        assert client.get(f"{_base(item_id)}/runs/preview?workflow_id=nope").status_code == 422


def test_parallel_runs_each_open_their_own_chat():
    """Topic-hub P8 (manual §3): the one-active-run-per-item rule is lifted — a second
    run launches in parallel, in its own workflow chat, even while the first is paused."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')  # first run parks at the gate
        first = client.post(f"{_base(item_id)}/run").json()
        _poll(client, item_id, first["run_id"], "awaiting_human")
        second = client.post(f"{_base(item_id)}/run")
        assert second.status_code == 202
        body = second.json()
        assert body["run_id"] != first["run_id"]
        assert body["chat_id"] != first["chat_id"]  # each run drives its own chat


def test_run_drives_its_own_workflow_chat_and_persists_there():
    """P8 (manual §3): a run opens a workflow chat (a Conversation with run_id) and its
    agent node's turn persists THERE — not the item's default chat."""
    app, spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 2}')
        resp = client.post(f"{_base(item_id)}/run").json()
        run_id, chat_id = resp["run_id"], resp["chat_id"]
        _poll(client, item_id, run_id, "done")
        chats = {c["chat_id"]: c for c in client.get(f"{_base(item_id)}/chats").json()}
    assert chat_id in chats
    assert chats[chat_id]["run_id"] == run_id  # a workflow chat, not the default
    assert chats[chat_id]["is_default"] is False
    conv = spec.get_resource_manager(Conversation).get(chat_id).data
    assert conv.run_id == run_id
    assert any(m.role == "assistant" and m.content == "ack" for m in conv.messages)


def test_two_parallel_runs_both_complete_sharing_the_filestore():
    """P8 (manual §3, §3.1): two runs proceed concurrently in one item (two chats);
    both reach done. Their shared note.json write is last-write-wins (no torn write)."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 1}')
        r1 = client.post(f"{_base(item_id)}/run").json()
        r2 = client.post(f"{_base(item_id)}/run").json()
        assert r1["chat_id"] != r2["chat_id"]
        d1 = _poll(client, item_id, r1["run_id"], "done")
        d2 = _poll(client, item_id, r2["run_id"], "done")
    assert d1["result"]["status"] == "done"
    assert d2["result"]["status"] == "done"


def test_free_chat_edit_unblocks_a_paused_workflow_via_shared_filestore():
    """P8 (manual §3.1): while a workflow chat is paused at a human gate, the shared
    FileStore is editable (the sandbox is freed during the pause); the edit lands and
    the workflow resumes to done."""
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')
        run = client.post(f"{_base(item_id)}/run").json()
        _poll(client, item_id, run["run_id"], "awaiting_human")
        # Edit the workspace file the paused run is "waiting on" (e.g. a glossary fill-in).
        assert (
            client.put(f"{_base(item_id)}/files/glossary.todo.md", content=b"done").status_code
            == 204
        )
        assert client.get(f"{_base(item_id)}/files/glossary.todo.md").content == b"done"
        client.post(f"{_base(item_id)}/runs/{run['run_id']}/decisions", json={"choice": "approve"})
        data = _poll(client, item_id, run["run_id"], "done")
    assert data["result"]["status"] == "approved"


def test_run_list_newest_first():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 1}')
        r1 = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, r1, "done")
        r2 = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, r2, "done")
        runs = client.get(f"{_base(item_id)}/runs").json()
    assert [r["run_id"] for r in runs][:2] == [r2, r1]


def test_human_gate_decision_approve_resumes_to_done():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true, "n": 5}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "awaiting_human")
        assert data["pending_decision"]["phase"] == "review"
        r = client.post(
            f"{_base(item_id)}/runs/{run_id}/decisions",
            json={"choice": "approve", "input": "ship it"},
        )
        assert r.status_code == 202
        data = _poll(client, item_id, run_id, "done")
    assert data["result"] == {"status": "approved", "n": 5, "note": "ship it"}


def test_human_gate_reject_ends_run():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "awaiting_human")
        client.post(f"{_base(item_id)}/runs/{run_id}/decisions", json={"choice": "reject"})
        data = _poll(client, item_id, run_id, "done")
    assert data["result"]["status"] == "rejected"


def test_decision_on_terminal_run_is_409():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 1}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "done")  # terminal, not awaiting
        r = client.post(f"{_base(item_id)}/runs/{run_id}/decisions", json={"choice": "approve"})
    assert r.status_code == 409


def test_cancel_running_gate_run():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "awaiting_human")
        assert client.post(f"{_base(item_id)}/runs/{run_id}/cancel").status_code == 204
        # cancel on a paused/terminal run is a no-op 204 (Stop is idempotent)
        assert client.post(f"{_base(item_id)}/runs/{run_id}/cancel").status_code == 204


def test_run_get_serializes_workflow_id():
    """The run GET response carries the durable `workflow_id` (P8) so the FE can map
    a run back to its profile's declared phases for the linear step bar."""
    app, _spec, item_id = _app(profile="multi")
    with TestClient(app) as client:
        _put_input(client, item_id, "{}")
        run_id = client.post(f"{_base(item_id)}/run?workflow_id=alpha").json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
    assert data["workflow_id"] == "alpha"


def test_get_unknown_run_is_404():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        assert client.get(f"{_base(item_id)}/runs/nope").status_code == 404


def test_run_with_sandbox_node_and_ingest_commits():
    """Exercises a deterministic sandbox node (credential injected into its env) and
    the in-process ingest + collection_has verify through the real wiring."""
    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    with TestClient(app) as client:
        _put_input(client, item_id, f'{{"sandbox": true, "ingest": "{cid}"}}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
    assert data["result"]["status"] == "done"
    assert data["result"]["landed"] is True  # collection_has verified the ingest landed


def test_run_finds_the_card_an_upsert_would_overwrite():
    """#205: the read-only find-overwrite-target capability resolves, through the real
    wiring, the existing card a commit-time upsert would overwrite (a hit returns its real
    keys/title/body + ambiguity; a non-matching key returns None)."""
    from workspace_app.workflow.capabilities import create_context_card

    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    create_context_card(
        spec, collection=cid, keys=["M4", "Metal 4"], title="Metal 4 layer", body="old", user="u"
    )
    with TestClient(app) as client:
        _put_input(client, item_id, f'{{"find_card": "{cid}", "keys": ["M4"], "title": "M4"}}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
    found = data["result"]["found"]
    assert found["title"] == "Metal 4 layer" and found["body"] == "old"
    assert sorted(found["keys"]) == ["M4", "Metal 4"] and found["ambiguity"] == 1
    assert data["result"]["miss"] is None  # a key that names no card → None


def test_run_journals_under_the_workflow_dir_not_root():
    """#136 end-to-end wiring: a run's journal artifacts — engine step records AND the
    ingest receipt — live under /.workflow/<workflow_id>/ (here _default, echo being a
    legacy singular workflow), with none left scattered at the workspace root."""
    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    with TestClient(app) as client:
        _put_input(client, item_id, f'{{"ingest": "{cid}"}}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "done")
        paths = [f["path"] for f in client.get(f"{_base(item_id)}/files").json()]
    assert any(p.startswith("/.workflow/_default/step_") for p in paths)  # journal moved
    assert not any(p.startswith("/step_") for p in paths)  # nothing left at root


def test_capability_ingest_lands_a_doc():
    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    with TestClient(app) as client:
        r = client.put(f"{_base(item_id)}/files/digest/a.md", content=b"# A\nhello content")
        assert r.status_code == 204
        r = client.post(
            f"{_base(item_id)}/capabilities/ingest",
            json={"collection": cid, "path": "digest/a.md"},
        )
    assert r.status_code == 200 and r.json()["doc_id"]


def test_capability_ingest_unknown_collection_404():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        client.put(f"{_base(item_id)}/files/a.md", content=b"x")
        r = client.post(
            f"{_base(item_id)}/capabilities/ingest",
            json={"collection": "no-such", "path": "a.md"},
        )
    assert r.status_code == 404


def test_capability_ingest_rejects_a_bad_token():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/capabilities/ingest",
            json={"collection": "c", "path": "a.md"},
            headers={"X-Workflow-Token": "forged"},
        )
    assert r.status_code == 401


def test_capability_context_card_creates_a_card():
    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/capabilities/context-card",
            json={"collection": cid, "keys": ["M4", "Metal 4"], "title": "Metal 4", "body": "L4"},
        )
    assert r.status_code == 200 and r.json()["card_id"]
    from workspace_app.kb.context_cards import lookup

    assert lookup(spec, cid, ["m4"])["m4"]  # the card is findable by exact key


def test_capability_context_card_unknown_collection_404():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/capabilities/context-card",
            json={"collection": "no-such", "keys": ["x"], "body": "b"},
        )
    assert r.status_code == 404


def test_capability_context_card_rejects_a_bad_token():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/capabilities/context-card",
            json={"collection": "c", "keys": ["x"], "body": "b"},
            headers={"X-Workflow-Token": "forged"},
        )
    assert r.status_code == 401


async def test_run_stream_endpoint_returns_an_sse_response():
    """The run stream reuses the item's broadcast SSE (manual §14). Invoke the
    handler directly — its body is the item's (never-ending) event stream, which the
    in-process transport would buffer; we assert the wiring + media type."""
    app, _spec, item_id = _app()
    resp = await _route(app, "/a/{slug}/items/{item_id}/runs/{run_id}/stream")(
        slug="playground", item_id=item_id, run_id="any"
    )
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
