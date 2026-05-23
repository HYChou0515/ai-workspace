"""Phase 9 — POST/DELETE /investigations/{id}/notebooks/{path}/cells/{idx}/execute
and POST /investigations/{id}/notebooks/{path}/kernel/restart.

The endpoints stream CellEvent JSON over SSE. {idx} is a UI handle
(which cell to highlight as running); the kernel doesn't care about
it, the BE just rounds-trips it through.
"""

from __future__ import annotations

import json

from .conftest import Harness


def _parse_sse(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))
    return events


def test_execute_print_emits_cell_stream_and_done(harness: Harness):
    resp = harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "print('hi')"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "cell_stream" in types
    assert types[-1] == "cell_done"
    stream = next(e for e in events if e["type"] == "cell_stream")
    assert stream["stream"] == "stdout"
    assert "hi" in stream["text"]


def test_execute_expression_emits_display_data(harness: Harness):
    resp = harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "1 + 1"},
    )
    events = _parse_sse(resp.text)
    displays = [e for e in events if e["type"] == "cell_display_data"]
    assert any("2" in (e["data"].get("text/plain") or "") for e in displays)


def test_execute_bad_code_emits_cell_error(harness: Harness):
    resp = harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "raise ValueError('boom')"},
    )
    events = _parse_sse(resp.text)
    errs = [e for e in events if e["type"] == "cell_error"]
    assert len(errs) == 1
    assert errs[0]["ename"] == "ValueError"
    assert "boom" in errs[0]["evalue"]


def test_execute_state_persists_across_cells_in_same_notebook(harness: Harness):
    """Two POSTs to the same notebook hit the same kernel —
    `x = 42` set in cell 0 is visible to `print(x)` in cell 1."""
    harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "x = 42"},
    )
    resp = harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/1/execute",
        json={"code": "print(x)"},
    )
    events = _parse_sse(resp.text)
    streams = [e for e in events if e["type"] == "cell_stream"]
    assert any("42" in e["text"] for e in streams)


def test_execute_different_notebooks_isolated(harness: Harness):
    """`y = 7` in a.ipynb is not visible from b.ipynb — distinct kernels."""
    harness.client.post(
        "/investigations/inv-1/notebooks/a.ipynb/cells/0/execute",
        json={"code": "y = 7"},
    )
    resp = harness.client.post(
        "/investigations/inv-1/notebooks/b.ipynb/cells/0/execute",
        json={"code": "print(y)"},
    )
    events = _parse_sse(resp.text)
    errs = [e for e in events if e["type"] == "cell_error"]
    assert any(e["ename"] == "NameError" for e in errs)


def test_execute_nested_notebook_path_supported(harness: Harness):
    """Notebook paths can include slashes (the FE saves nested files)."""
    resp = harness.client.post(
        "/investigations/inv-1/notebooks/sub/dir/deep.ipynb/cells/0/execute",
        json={"code": "print('deep')"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    streams = [e for e in events if e["type"] == "cell_stream"]
    assert any("deep" in e["text"] for e in streams)


def test_delete_cell_execute_interrupts_returns_204(harness: Harness):
    # Spin up the kernel first so DELETE has something to talk to.
    harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "1"},
    )
    resp = harness.client.delete("/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute")
    assert resp.status_code == 204


def test_delete_cell_execute_on_unknown_kernel_returns_204(harness: Harness):
    """Idempotent: hitting interrupt for a kernel that doesn't exist
    is a noop (the user might double-click Stop)."""
    resp = harness.client.delete("/investigations/inv-1/notebooks/missing.ipynb/cells/0/execute")
    assert resp.status_code == 204


def test_post_kernel_restart_returns_204_and_clears_state(harness: Harness):
    harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "x = 42"},
    )
    resp = harness.client.post("/investigations/inv-1/notebooks/nb.ipynb/kernel/restart")
    assert resp.status_code == 204
    # After restart, x is gone.
    after = harness.client.post(
        "/investigations/inv-1/notebooks/nb.ipynb/cells/0/execute",
        json={"code": "print(x)"},
    )
    events = _parse_sse(after.text)
    errs = [e for e in events if e["type"] == "cell_error"]
    assert any(e["ename"] == "NameError" for e in errs)


def test_post_kernel_restart_on_unknown_notebook_returns_204(harness: Harness):
    """Restarting a kernel that hasn't been started yet just starts one."""
    resp = harness.client.post("/investigations/inv-1/notebooks/never.ipynb/kernel/restart")
    assert resp.status_code == 204
