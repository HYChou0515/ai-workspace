"""Save a marking as a table (plan-view-plugins-pr5-finish P7).

`POST /a/{slug}/items/{id}/markings/table` runs the chart plugin's `lit_rows`
in the item's sandbox — through the same runner the chart's `query` takes —
over the view the action was taken in, then writes the CSV it answers to
`/markings/<name>-<stamp>.csv` through the file facade: the workspace quota
applies, and the caller must hold `add_content`. Which rows are lit is the
sandbox half's job, pinned against pandas in the plugin's own tests; here the
sandbox answers what the test says it does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from workspace_app.api import create_app
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.api.view_plugin_routes import ARGV_MAX
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.quota.disk_ledger import UserDiskFull
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ExecResult, SandboxBusy, SandboxNotFound
from workspace_app.view_plugins import discover_view_plugins

CSV = "lot,wafer,value\nA,1,0.5\nC,3,7.0\n"


class _LitSandbox(MockSandbox):
    """A MockSandbox whose chart bundle answers `lit_rows` as told."""

    def __init__(self, answer: ExecResult | Exception | None = None) -> None:
        super().__init__()
        self.answer = answer or ExecResult(
            exit_code=0, stdout=json.dumps({"rows": 2, "csv": CSV}).encode(), stderr=b""
        )
        self.lit_calls: list[list[str]] = []

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None):
        if cmd[:2] == ["../.tools/chart/launch", "lit_rows"]:
            self.lit_calls.append(list(cmd))
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        return await super().exec(handle, cmd, on_output, env, exec_timeout)


def _plugins(tmp_path: Path, *, chart: bool = True):
    root = tmp_path / "plugins"
    root.mkdir()
    if chart:
        d = root / "chart"
        (d / "web").mkdir(parents=True)
        (d / "web" / "index.js").write_text("export {};\n")
        manifest = {"name": "chart", "sdk": "1", "kinds": ["chart"], "sandbox": {"bundle": "x"}}
        (d / "plugin.json").write_text(json.dumps(manifest))
    return discover_view_plugins(root)


def _app(tmp_path: Path, sandbox: MockSandbox | None = None, *, chart: bool = True, **kw):
    spec = make_spec(default_user=kw.pop("default_user", "u"))
    sb = sandbox or _LitSandbox()
    app = create_app(
        spec=spec,
        sandbox=sb,
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        view_plugins=_plugins(tmp_path, chart=chart),
        **kw,
    )
    return TestClient(app), spec, sb


BODY = {
    "name": "fail",
    "view": "/views/c.ai.yaml",
    "columns": {"lot": ["A", "C"]},
    "stamp": "20260925-1412",
}


def _url(iid: str) -> str:
    return f"/a/rca/items/{iid}/markings/table"


def test_the_lit_rows_land_as_a_new_csv_in_the_workspace(tmp_path):
    client, spec, sb = _app(tmp_path)
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 200, r.text
    assert r.json() == {"path": "/markings/fail-20260925-1412.csv", "rows": 2}
    got = client.get(f"/a/rca/items/{iid}/files/markings/fail-20260925-1412.csv")
    assert got.status_code == 200 and got.text == CSV
    [call] = sb.lit_calls
    assert json.loads(call[2]) == {"view": "/views/c.ai.yaml", "columns": {"lot": ["A", "C"]}}


def test_the_chip_sends_no_values_and_the_sandbox_reads_the_sent_marking(tmp_path):
    client, spec, sb = _app(tmp_path)
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json={**BODY, "columns": None})

    assert r.status_code == 200, r.text
    [call] = sb.lit_calls
    assert json.loads(call[2]) == {"view": "/views/c.ai.yaml", "marking": "/.markings/fail.json"}


def test_a_second_save_in_the_same_minute_gets_its_own_file(tmp_path):
    client, spec, _ = _app(tmp_path)
    iid = register_rca_item(spec)

    first = client.post(_url(iid), json=BODY).json()["path"]
    second = client.post(_url(iid), json=BODY).json()["path"]

    assert (first, second) == (
        "/markings/fail-20260925-1412.csv",
        "/markings/fail-20260925-1412-2.csv",
    )


def test_a_minute_that_has_run_out_of_names_is_refused(tmp_path, monkeypatch):
    from workspace_app.api import marking_table

    monkeypatch.setattr(marking_table, "MAX_SUFFIX", 2)
    client, spec, _ = _app(tmp_path)
    iid = register_rca_item(spec)
    assert client.post(_url(iid), json=BODY).status_code == 200
    assert client.post(_url(iid), json=BODY).status_code == 200

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 409
    assert r.json()["detail"] == "too many tables were saved from this marking this minute"


def test_the_sandboxs_refusal_is_the_controls_sentence_and_nothing_is_written(tmp_path):
    no_rows = ExecResult(
        exit_code=2, stdout=b"", stderr=b"no row of the view is lit by the marking\n"
    )
    client, spec, _ = _app(tmp_path, _LitSandbox(no_rows))
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 422
    assert r.json()["detail"] == "no row of the view is lit by the marking"
    assert (
        client.get(f"/a/rca/items/{iid}/files/markings/fail-20260925-1412.csv").status_code == 404
    )


@pytest.mark.parametrize(
    "result",
    [
        ExecResult(exit_code=1, stdout=b"", stderr=b"Traceback (most recent call last): /.tools/x"),
        ExecResult(exit_code=0, stdout=b"not json", stderr=b""),
        ExecResult(exit_code=0, stdout=b'{"rows": "2", "csv": "a"}', stderr=b""),
        ExecResult(exit_code=0, stdout=b'{"rows": 2, "csv": 5}', stderr=b""),
        ExecResult(exit_code=0, stdout=b'{"rows": 2}', stderr=b""),
    ],
)
def test_a_crash_or_a_garbled_answer_says_so_without_its_internals(tmp_path, result):
    client, spec, _ = _app(tmp_path, _LitSandbox(result))
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 502
    assert r.json()["detail"] == "the rows could not be selected"


def test_a_full_workspace_refuses_the_save_on_the_control(tmp_path):
    client, spec, _ = _app(tmp_path, workspace_quota=10)
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 507
    assert r.json()["detail"].startswith("workspace is full")
    assert (
        client.get(f"/a/rca/items/{iid}/files/markings/fail-20260925-1412.csv").status_code == 404
    )


def test_an_unreachable_sandbox_is_a_retry_not_a_crash(tmp_path):
    client, spec, _ = _app(tmp_path, _LitSandbox(SandboxNotFound("h-123 gone")))
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 503
    assert r.json()["detail"] == "the workspace could not be reached — try again"


def test_a_marking_too_big_for_one_call_says_to_send_it_from_the_chat(tmp_path):
    client, spec, sb = _app(tmp_path)
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json={**BODY, "columns": {"lot": ["x" * ARGV_MAX]}})

    assert r.status_code == 413
    assert "send it in the chat" in r.json()["detail"]
    assert sb.lit_calls == []


def test_without_the_chart_plugin_the_save_is_unavailable(tmp_path):
    client, spec, _ = _app(tmp_path, chart=False)
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 501
    assert r.json()["detail"] == "saving a marking as a table is not available in this deployment"


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"name": ""}, "a marking needs a name"),
        ({"name": "a/b"}, "the name cannot be used as a file name"),
        ({"name": "é" * 120}, "the name is too long for a file name"),
        ({"stamp": "2026-09-25 14:12"}, "the time stamp must be yyyymmdd-hhmm"),
        ({"view": "  "}, "this marking has no view to take its rows from"),
    ],
)
def test_a_save_that_cannot_be_named_is_refused_before_the_sandbox_runs(tmp_path, change, said):
    client, spec, sb = _app(tmp_path)
    iid = register_rca_item(spec)

    r = client.post(_url(iid), json={**BODY, **change})

    assert r.status_code == 422
    assert r.json()["detail"] == said
    assert sb.lit_calls == []


class _Files:
    """A facade double: `write` raises what it is told to."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.written: dict[str, bytes] = {}

    async def exists(self, item_id: str, path: str) -> bool:
        return path in self.written

    async def write(self, item_id: str, path: str, data: bytes) -> None:
        if self.raises is not None:
            raise self.raises
        self.written[path] = data


class _Locator:
    def __init__(self, refuse: dict[str, HTTPException] | None = None) -> None:
        self.refuse = refuse or {}

    def require_access(self, slug: str, item_id: str, verb: str) -> str:
        if verb in self.refuse:
            raise self.refuse[verb]
        return item_id


def _bare(files=None, locator=None, run=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as Plain

    from workspace_app.api.marking_table import register_marking_table_route
    from workspace_app.api.view_plugin_routes import RunOut

    async def answer(item_id, plugin, cmd, args):
        if isinstance(run, Exception):
            raise run
        return RunOut(stdout=json.dumps({"rows": 2, "csv": CSV}), stderr="", exit_code=0)

    app = FastAPI()
    register_marking_table_route(
        app,
        locator=locator or _Locator(),
        files=cast("WorkspaceFiles", files or _Files()),
        run_plugin=answer,
    )
    return Plain(app)


@pytest.mark.parametrize(
    ("raises", "status", "said"),
    [
        (SandboxBusy("still starting"), 503, "the workspace could not be reached — try again"),
        (
            IsADirectoryError(21, "Is a directory"),
            409,
            "the table could not be saved: Is a directory",
        ),
        (OSError("no errno"), 409, "the table could not be saved: the disk refused it"),
        (UserDiskFull("u", 10, 20, 5), 507, None),
    ],
)
def test_a_write_the_workspace_refuses_is_a_sentence(raises, status, said):
    client = _bare(files=_Files(raises))

    r = client.post(_url("i1"), json=BODY)

    assert r.status_code == status
    assert r.json()["detail"] == (said if said is not None else str(raises))


def test_an_item_the_caller_cannot_see_stays_not_found():
    missing = HTTPException(status_code=404, detail="item not found")
    client = _bare(locator=_Locator({"read_content": missing}))
    assert client.post(_url("i1"), json=BODY).status_code == 404
    client = _bare(locator=_Locator({"add_content": missing}))
    assert client.post(_url("i1"), json=BODY).status_code == 404


def test_the_runners_other_refusals_pass_through_as_they_are():
    client = _bare(run=HTTPException(status_code=409, detail="rename the plugin"))

    r = client.post(_url("i1"), json=BODY)

    assert r.status_code == 409 and r.json()["detail"] == "rename the plugin"


def test_saving_asks_add_content_and_says_so_in_words(tmp_path):
    from workspace_app.apps.rca.model import RcaInvestigation
    from workspace_app.perm import Permission

    holder = {"id": "bob"}
    spec = make_spec(default_user=lambda: holder["id"])
    sb = _LitSandbox()
    app = create_app(
        spec=spec,
        sandbox=sb,
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        view_plugins=_plugins(tmp_path),
        get_user_id=lambda: holder["id"],
    )
    client = TestClient(app)
    carol = ["user:carol"]
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        iid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(visibility="restricted", read_meta=carol, read_content=carol),
            )
        ).resource_id
    holder["id"] = "carol"

    r = client.post(_url(iid), json=BODY)

    assert r.status_code == 403
    assert r.json()["detail"] == "you may not add files in this workspace"
    assert sb.lit_calls == []
