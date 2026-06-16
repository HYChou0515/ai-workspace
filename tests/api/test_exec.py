"""POST /a/{slug}/items/{id}/exec — direct sandbox shell from the UI.

Lets the FE's Terminal pane run shell commands inside the investigation's
sandbox without going through the agent. Returns ExecResult JSON, then
reverse-syncs the sandbox so any newly-created files show up in the
sidebar on next listFiles().
"""

from __future__ import annotations

from .conftest import Harness


def test_exec_echo_returns_exit_code_and_stdout(harness: Harness):
    resp = harness.client.post(
        harness.wpath("/exec"),
        json={"cmd": ["echo", "hello"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_code"] == 0
    assert body["stdout"] == "hello\n"
    assert body["stderr"] == ""


def test_exec_false_returns_nonzero_exit(harness: Harness):
    resp = harness.client.post(
        harness.wpath("/exec"),
        json={"cmd": ["false"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_code"] == 1


def test_exec_rejects_empty_cmd(harness: Harness):
    resp = harness.client.post(
        harness.wpath("/exec"),
        json={"cmd": []},
    )
    assert resp.status_code == 422


def test_exec_rejects_non_array_cmd(harness: Harness):
    resp = harness.client.post(
        harness.wpath("/exec"),
        json={"cmd": "echo hello"},
    )
    assert resp.status_code == 422


def test_exec_creates_sandbox_lazily(harness: Harness):
    """First exec spins up the session+sandbox; second exec reuses it
    (we observe this by confirming both succeed without errors)."""
    a = harness.client.post(harness.wpath("/exec"), json={"cmd": ["echo", "a"]})
    b = harness.client.post(harness.wpath("/exec"), json={"cmd": ["echo", "b"]})
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["stdout"] == "a\n"
    assert b.json()["stdout"] == "b\n"


def test_exec_unknown_command_reports_127(harness: Harness):
    """MockSandbox returns 127 for unknown commands; this matches a
    real shell's exit code for "command not found"."""
    resp = harness.client.post(
        harness.wpath("/exec"),
        json={"cmd": ["definitely-not-a-real-binary"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_code"] == 127


def test_exec_unexpected_sandbox_error_returns_200(harness: Harness, monkeypatch):
    """The endpoint must NEVER 500 on a sandbox-side failure — the Terminal
    pane has nowhere to show an HTTP error. Any unexpected exception from
    sandbox.exec becomes a structured 200 body with the error in stderr."""
    from workspace_app.sandbox.mock import MockSandbox

    async def boom(*_a, **_kw):
        raise RuntimeError("kernel went away")

    monkeypatch.setattr(MockSandbox, "exec", boom)

    resp = harness.client.post(
        harness.wpath("/exec"),
        json={"cmd": ["echo", "x"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Real-ish exit code (non-zero, NOT a made-up 0); stderr explains.
    assert body["exit_code"] != 0
    assert body["stdout"] == ""
    assert "kernel went away" in body["stderr"]
    assert "RuntimeError" in body["stderr"]
