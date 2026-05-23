"""GET /activity — recent-activity feed behind the Home notifications
popover. Events recorded as the user / agent acts on investigations.
"""

from __future__ import annotations

from .conftest import Harness


def test_activity_empty_initially(harness: Harness):
    resp = harness.client.get("/activity")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_investigation_records_activity(harness: Harness):
    harness.client.post("/investigation", json={"title": "Voids spike", "owner": "alice"})
    feed = harness.client.get("/activity").json()
    assert len(feed) == 1
    assert feed[0]["kind"] == "investigation_created"
    assert "Voids spike" in feed[0]["text"]
    assert feed[0]["ref"]["investigation_id"].startswith("investigation:")


def test_close_investigation_records_activity(harness: Harness):
    inv_id = harness.client.post("/investigation", json={"title": "x", "owner": "alice"}).json()[
        "resource_id"
    ]
    harness.client.post(f"/investigations/{inv_id}/close", json={"status": "resolved"})
    feed = harness.client.get("/activity").json()
    kinds = [e["kind"] for e in feed]
    assert "investigation_closed" in kinds
    closed = next(e for e in feed if e["kind"] == "investigation_closed")
    assert "resolved" in closed["text"]


def test_put_file_records_activity(harness: Harness):
    harness.client.put("/investigations/ws-a/files/notes.txt", content=b"hi")
    feed = harness.client.get("/activity").json()
    assert feed[0]["kind"] == "file_written"
    assert "/notes.txt" in feed[0]["text"]
    assert feed[0]["ref"]["path"] == "/notes.txt"


def test_delete_and_move_record_activity(harness: Harness):
    harness.client.put("/investigations/ws-a/files/a.txt", content=b"x")
    harness.client.post("/investigations/ws-a/files/move", json={"from": "/a.txt", "to": "/b.txt"})
    harness.client.delete("/investigations/ws-a/files/b.txt")
    kinds = [e["kind"] for e in harness.client.get("/activity").json()]
    assert "file_moved" in kinds
    assert "file_deleted" in kinds


def test_agent_turn_records_activity(harness: Harness):
    # Scripted runner ends with RunDone → one agent_turn_complete entry.
    resp = harness.client.post("/investigations/ws-1/messages", json={"content": "hi"})
    assert resp.status_code == 200
    _ = resp.text  # drain the stream so gen() runs to completion
    kinds = [e["kind"] for e in harness.client.get("/activity").json()]
    assert "agent_turn_complete" in kinds


def test_activity_is_newest_first(harness: Harness):
    harness.client.put("/investigations/ws-a/files/one.txt", content=b"1")
    harness.client.put("/investigations/ws-a/files/two.txt", content=b"2")
    feed = harness.client.get("/activity").json()
    assert feed[0]["ref"]["path"] == "/two.txt"
    assert feed[1]["ref"]["path"] == "/one.txt"
