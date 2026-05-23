"""POST /investigations/{id}/close — manual close + status update.

Per plan-backend §6: status transitions to resolved/abandoned, sandbox
torn down, session evicted from the registry.
"""

from workspace_app.resources import Investigation, Status

from .conftest import Harness


def _create_investigation(harness: Harness, **fields: object) -> str:
    body: dict = {"title": "x", "owner": "default-user", **fields}
    resp = harness.client.post("/investigation", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["resource_id"]


def test_close_endpoint_updates_status_to_resolved(harness: Harness):
    inv_id = _create_investigation(harness, title="closes ok")
    resp = harness.client.post(f"/investigations/{inv_id}/close", json={"status": "resolved"})
    assert resp.status_code == 204
    rm = harness.spec.get_resource_manager(Investigation)
    got = rm.get(inv_id).data
    assert isinstance(got, Investigation)
    assert got.status is Status.RESOLVED


def test_close_endpoint_updates_status_to_abandoned(harness: Harness):
    inv_id = _create_investigation(harness, title="dead end")
    resp = harness.client.post(f"/investigations/{inv_id}/close", json={"status": "abandoned"})
    assert resp.status_code == 204
    rm = harness.spec.get_resource_manager(Investigation)
    got = rm.get(inv_id).data
    assert isinstance(got, Investigation)
    assert got.status is Status.ABANDONED


def test_close_endpoint_rejects_invalid_target_status(harness: Harness):
    """Only resolved | abandoned are valid close targets — triaging
    or awaiting_review aren't terminal."""
    inv_id = _create_investigation(harness, title="x")
    resp = harness.client.post(f"/investigations/{inv_id}/close", json={"status": "triaging"})
    assert resp.status_code in (400, 422)


def test_close_endpoint_for_unknown_investigation_returns_404(harness: Harness):
    resp = harness.client.post("/investigations/no-such-id/close", json={"status": "resolved"})
    assert resp.status_code == 404


def test_pure_close_leaves_status_unchanged(harness: Harness):
    """Closing with no status (or null) just tears the session down —
    the investigation stays open (triaging)."""
    inv_id = _create_investigation(harness, title="still open")
    resp = harness.client.post(f"/investigations/{inv_id}/close", json={})
    assert resp.status_code == 204
    rm = harness.spec.get_resource_manager(Investigation)
    got = rm.get(inv_id).data
    assert isinstance(got, Investigation)
    assert got.status is Status.TRIAGING

    resp_null = harness.client.post(f"/investigations/{inv_id}/close", json={"status": None})
    assert resp_null.status_code == 204
    assert rm.get(inv_id).data.status is Status.TRIAGING
