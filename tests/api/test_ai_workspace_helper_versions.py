"""The helper and the backend agree on a version (docs/plan-outside-helper.md
§3.7.3): every helper request says its version and protocol; one the backend
no longer accepts gets a 426 carrying where the current release is — an
explicit "please update", not something the helper could mistake for the
network being down."""

from __future__ import annotations

import pytest

from tests.api.test_ai_workspace_helper_release import _publish
from tests.api.test_outside_answers import _app
from workspace_app.api.outside_jobs import MIN_PROTOCOL


def _headers(version="0.3.0", protocol=MIN_PROTOCOL):
    return {
        "X-AI-Workspace-Helper-Version": version,
        "X-AI-Workspace-Helper-Protocol": str(protocol),
    }


_HELPER_CALLS = [
    ("post", "/ai-workspace-helper/heartbeat", {"version": "x"}),
    ("post", "/ai-workspace-helper/jobs/claim", None),
    ("post", "/ai-workspace-helper/jobs/j1/result", {"markdown": "x"}),
    ("post", "/ai-workspace-helper/jobs/j1/fail", {"error": "x"}),
    ("post", "/ai-workspace-helper/jobs/j1/note", {"note": "x"}),
]


@pytest.mark.parametrize(("method", "path", "body"), _HELPER_CALLS)
def test_a_version_below_the_minimum_is_told_to_update(tmp_path, method, path, body):
    manifest, _ = _publish(tmp_path, "0.3.0", min_version="0.2.0")
    client, _spec = _app(ai_workspace_helper_dir=str(tmp_path))

    r = getattr(client, method)(path, json=body, headers=_headers(version="0.1.9"))

    assert r.status_code == 426, r.text
    assert r.json()["detail"]["release"]["version"] == "0.3.0"
    assert r.json()["detail"]["release"]["file"] == manifest["file"]


@pytest.mark.parametrize(("method", "path", "body"), _HELPER_CALLS)
def test_a_protocol_below_the_minimum_is_told_to_update(tmp_path, method, path, body):
    _publish(tmp_path, "0.3.0", min_version="0.0.0")
    client, _spec = _app(ai_workspace_helper_dir=str(tmp_path))

    r = getattr(client, method)(path, json=body, headers=_headers(protocol=MIN_PROTOCOL - 1))

    assert r.status_code == 426, r.text


def test_the_minimum_version_itself_is_accepted(tmp_path):
    _publish(tmp_path, "0.3.0", min_version="0.2.0")
    client, _spec = _app(ai_workspace_helper_dir=str(tmp_path))

    r = client.post("/ai-workspace-helper/heartbeat", json={}, headers=_headers(version="0.2.0"))

    assert r.status_code == 204, r.text


# 0.10.0 is newer than 0.9.0 — compared as numbers, not as text.
def test_versions_compare_as_numbers(tmp_path):
    _publish(tmp_path, "0.10.0", min_version="0.9.0")
    client, _spec = _app(ai_workspace_helper_dir=str(tmp_path))

    assert (
        client.post(
            "/ai-workspace-helper/heartbeat", json={}, headers=_headers("0.10.0")
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/ai-workspace-helper/heartbeat", json={}, headers=_headers("0.8.12")
        ).status_code
        == 426
    )


# Refused, an old helper must still be able to fetch what replaces it.
def test_an_old_helper_can_still_fetch_the_update(tmp_path):
    manifest, zip_bytes = _publish(tmp_path, "0.3.0", min_version="0.3.0")
    client, _spec = _app(ai_workspace_helper_dir=str(tmp_path))
    old = _headers(version="0.1.0", protocol=0)

    assert client.get("/ai-workspace-helper/manifest", headers=old).status_code == 200
    d = client.get(f"/ai-workspace-helper/download/{manifest['file']}", headers=old)
    assert d.content == zip_bytes


# With nothing published there is no minimum version — only the protocol.
def test_with_nothing_published_only_the_protocol_is_checked():
    client, _spec = _app()

    assert (
        client.post(
            "/ai-workspace-helper/heartbeat", json={}, headers=_headers("0.0.1")
        ).status_code
        == 204
    )
    r = client.post("/ai-workspace-helper/heartbeat", json={}, headers=_headers(protocol=0))
    assert r.status_code == 426
    assert r.json()["detail"]["release"] is None


# The card's own read is not a helper's request.
def test_the_cards_status_read_is_never_refused(tmp_path):
    _publish(tmp_path, "0.3.0", min_version="0.3.0")
    client, _spec = _app(ai_workspace_helper_dir=str(tmp_path))

    assert client.get("/ai-workspace-helper/status").status_code == 200


# §3.7.7: the backend sees who is on an old version, and whose Chrome has moved
# past what was tested.
def test_the_heartbeat_records_what_the_helper_runs_on():
    client, _spec = _app()
    store = client.app.state.outside_jobs

    client.post(
        "/ai-workspace-helper/heartbeat",
        json={
            "version": "0.3.0",
            "protocol": 1,
            "chrome": "141.0.7390.55",
            "playwright": "1.63.0",
            "python": "3.12.5",
            "update": "0.4.0 起不來,已退回 0.3.0",
        },
        headers=_headers(),
    )

    seen = store.presence("u")
    assert (seen.version, seen.chrome, seen.playwright, seen.python, seen.update) == (
        "0.3.0",
        "141.0.7390.55",
        "1.63.0",
        "3.12.5",
        "0.4.0 起不來,已退回 0.3.0",
    )
