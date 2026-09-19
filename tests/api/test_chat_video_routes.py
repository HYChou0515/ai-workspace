"""`POST /a/{slug}/items/{item_id}/chat-video` (plan-chat-video-export P6):
a complete transcript in the `.chat.json` shape, the options, an optional
output path — and back come the three paths the job will use and how long
the video will play. What this route does is decided by what it writes:
the source and the queued progress file are in the tree before it answers.
"""

from __future__ import annotations

import re

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.chat_video.jobs import ChatVideoJob
from workspace_app.chat_video.progress import Progress
from workspace_app.config.schema import ChatVideoSettings
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm.model import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

# Hand-written, the way the API is meant to be driven from a script: three
# messages a person typed into a JSON file, never exported from a real chat.
TRANSCRIPT = {
    "title": "OOM 事故",
    "messages": [
        {"role": "user", "content": "為什麼 API pod 會 OOM?"},
        {
            "role": "assistant",
            "content": "cluster_sweeper 在每顆 pod 讀全表。",
            "tool_calls": [],
        },
        {"role": "user", "content": "謝謝"},
    ],
}


def _client_and_spec(holder: dict[str, str], *, chat_video: ChatVideoSettings | None = None):
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        chat_video=chat_video,
        # The route QUEUES; what a consumer would then do with Chromium and
        # ffmpeg is the coordinator's business and tested there.
        run_consumers=False,
    )
    return TestClient(app), spec


def _item(spec, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title="t", owner=by, permission=permission)).resource_id


def _post(client: TestClient, iid: str, **body):
    return client.post(f"/a/rca/items/{iid}/chat-video", json={"transcript": TRANSCRIPT, **body})


def test_a_hand_written_transcript_is_queued_and_the_three_paths_come_back():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")

    r = _post(client, iid, options={"width": 1280, "height": 720, "fmt": ["mp4"]})

    assert r.status_code == 202, r.text
    body = r.json()
    out = body["output_path"]
    assert re.fullmatch(r"/exports/chat-video/OOM-事故-\d{8}-\d{6}\.mp4", out), out
    assert body["source_path"] == out + ".chat.json"
    assert body["progress_path"] == out + ".progress.json"
    assert body["expected_seconds"] > 0
    # The watcher's rule for "the worker stopped breathing" is the server's
    # (`chat_video.stale_after_seconds`), handed over rather than guessed.
    assert body["stale_after_seconds"] == 60
    # The two files are in the tree before the answer: the transcript, kept
    # (edit and resubmit), and the progress file the person will watch —
    # whose absence is the cancel, so it must exist to be deleted.
    assert client.get(f"/a/rca/items/{iid}/files{body['source_path']}").json() == TRANSCRIPT
    raw = client.get(f"/a/rca/items/{iid}/files{body['progress_path']}").content
    progress = Progress.loads(raw)
    assert progress.stage == "queued" and progress.requested_by == "bob"
    assert progress.expected_seconds == body["expected_seconds"]
    (job,) = [r.data for r in spec.get_resource_manager(ChatVideoJob).list_resources()]
    assert job.payload.output_path == out and job.payload.options.fmt == ("mp4",)


def test_the_output_paths_extension_picks_the_format_as_the_cli_does():
    """One rule for the file's name and its format — the CLI's `-o demo.gif`:
    the extension decides, and `options.fmt` follows it."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")

    r = _post(client, iid, output_path="videos/demo.gif", options={"fmt": ["mp4"]})

    assert r.status_code == 202, r.text
    assert r.json()["output_path"] == "/videos/demo.gif"
    (job,) = [r.data for r in spec.get_resource_manager(ChatVideoJob).list_resources()]
    assert job.payload.options.fmt == ("gif",)


@pytest.mark.parametrize(
    ("granted", "missing"),
    [("read_content", "add_content"), ("add_content", "read_content")],
)
def test_both_verbs_are_needed(granted, missing):
    """Reading the item's files (the transcript's pictures) AND adding to them
    (the video): a grantee holding one is refused for the other."""
    holder = {"id": "alice"}
    client, spec = _client_and_spec(holder)
    perm = Permission(
        visibility="restricted", read_meta=["user:alice"], **{granted: ["user:alice"]}
    )
    iid = _item(spec, by="bob", permission=perm)

    r = _post(client, iid)

    assert r.status_code == 403, r.text
    assert missing in r.json()["detail"]


def test_a_transcript_that_is_not_one_is_refused_with_the_part_named():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")

    r = client.post(
        f"/a/rca/items/{iid}/chat-video",
        json={"transcript": {"title": "t", "messages": [{"role": "user"}]}},
    )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == 'message 1 needs string "role" and "content"'


def test_an_option_the_struct_refuses_is_a_422_with_its_sentence():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")

    r = _post(client, iid, options={"speed": 0})

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "speed must be 0.1..100"


def test_a_size_past_this_deployments_ceiling_is_a_422_naming_the_ceiling():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder, chat_video=ChatVideoSettings(max_pixels=1280 * 720))
    iid = _item(spec, by="bob")

    r = _post(client, iid, options={"width": 1282, "height": 720})

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "1282×720 is 923,040 pixels; at most 921,600 (1280×720)"


@pytest.mark.parametrize(
    ("output_path", "status", "detail"),
    [
        ("../escape.mp4", 400, "path may not contain a '..' segment"),
        ("videos/demo.exe", 422, "output_path must end in .gif, .mp4 or .webm"),
        ("videos/demo", 422, "output_path must end in .gif, .mp4 or .webm"),
    ],
)
def test_an_output_path_the_workspace_cannot_take(output_path, status, detail):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")

    r = _post(client, iid, output_path=output_path)

    assert r.status_code == status, r.text
    assert r.json()["detail"] == detail


def test_an_output_path_already_taken_is_a_conflict():
    """A video is never written over a file that is there — the file routes'
    own answer for a move onto an occupied path."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    assert client.put(f"/a/rca/items/{iid}/files/videos/demo.mp4", content=b"x").status_code == 204

    r = _post(client, iid, output_path="/videos/demo.mp4")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "file exists at videos/demo.mp4"


def test_a_second_request_for_a_video_still_being_made_is_a_conflict():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    assert _post(client, iid, output_path="/videos/demo.mp4").status_code == 202

    r = _post(client, iid, output_path="/videos/demo.mp4")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "a video is already being made at /videos/demo.mp4"


def test_the_deployments_ceilings_are_readable_so_the_form_never_offers_past_them():
    """`GET` on the same path: the three numbers `check_limits` and the worker
    apply, for the dialog to hide the sizes this deployment refuses — a form
    that offers 2160p and then shows a 422 sentence is a form that lies."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(
        holder, chat_video=ChatVideoSettings(max_pixels=1280 * 720, max_output_bytes=5)
    )
    iid = _item(spec, by="bob")

    r = client.get(f"/a/rca/items/{iid}/chat-video")

    assert r.status_code == 200, r.text
    assert r.json() == {"max_pixels": 921600, "max_seconds": 180, "max_output_bytes": 5}


def test_a_stranger_to_a_private_item_learns_nothing_from_the_limits_route():
    holder = {"id": "carol"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))

    assert client.get(f"/a/rca/items/{iid}/chat-video").status_code == 404
