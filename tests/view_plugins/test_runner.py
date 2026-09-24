"""The generic view-plugin runner (#847/#848 PR1 P6).

`POST /a/{slug}/items/{id}/view-plugins/{plugin}/{cmd}` with `{args}` runs the
plugin bundle's `launch <cmd> <args_json>` in the ITEM's sandbox through
`exec_package_command` — the same funnel an agent's tool call and a WUI's
`callTool` take — and answers `{stdout, stderr, exit_code}`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.view_plugin_routes import ARGV_MAX, register_view_plugin_runner
from workspace_app.sandbox.protocol import ExecResult, Sandbox, SandboxHandle
from workspace_app.tooling.external import ExternalTools
from workspace_app.view_plugins import discover_view_plugins


class _Sandbox:
    def __init__(self, result: ExecResult | None = None, *, resolves_tools: bool = False):
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.result = result or ExecResult(exit_code=0, stdout=b'{"ok":1}', stderr=b"note")
        self.resolves_tools = resolves_tools

    async def exists(self, handle, path: str) -> bool:
        return False

    async def exec(self, handle, cmd, on_output=None, env=None):
        self.calls.append(list(cmd))
        self.envs.append(dict(env or {}))
        return self.result


class _Session:
    def __init__(self, handle=None, tools=None):
        self.handle = handle
        self.tools = tools


class _Registry:
    def __init__(self, session: _Session | None = None):
        self.tools: list[dict[str, str] | None] = []
        self._session = session or _Session()

    async def session(self, item_id: str) -> _Session:
        return self._session

    async def ensure_handle(self, session, *, tools=None, on_progress=None) -> SandboxHandle:
        self.tools.append(tools)
        return SandboxHandle(id="h1")

    async def prepare_project_env(self, session, handle, *, on_output=None) -> None:
        return None


class _Locator:
    def __init__(self):
        self.verbs: list[str] = []

    def require_access(self, slug: str, item_id: str, verb: str) -> str:
        self.verbs.append(verb)
        return item_id

    def env_vars_of(self, item_id: str) -> dict[str, str]:
        return {"API_KEY": "sk-1"}


def _plugins(tmp_path: Path, **sandboxes: dict | None):
    root = tmp_path / "plugins"
    for name, sb in sandboxes.items():
        d = root / name
        (d / "web").mkdir(parents=True)
        (d / "web" / "index.js").write_text("export {};\n")
        m: dict[str, Any] = {"name": name, "sdk": "1", "kinds": [name]}
        if sb is not None:
            m["sandbox"] = sb
        (d / "plugin.json").write_text(json.dumps(m))
    return discover_view_plugins(root)


def _client(tmp_path, *, sandbox=None, registry=None, external=None, locator=None, **plugins):
    app = FastAPI()
    found = _plugins(tmp_path, **(plugins or {"chart": {"bundle": "sandbox"}}))
    sb = sandbox or _Sandbox()
    reg = registry or _Registry()
    loc = locator or _Locator()

    async def resolve_tools(item_id: str) -> ExternalTools:
        return external or ExternalTools(shas={"app-tool": "a" * 64})

    register_view_plugin_runner(
        app,
        get_plugins=lambda: found,
        locator=loc,
        sandbox=cast("Sandbox", sb),
        registry=reg,
        resolve_tools=resolve_tools,
    )
    return TestClient(app), sb, reg, loc


URL = "/a/pm/items/i1/view-plugins/chart/summary"


def test_runs_the_plugin_launch_in_the_items_sandbox(tmp_path):
    client, sb, reg, loc = _client(tmp_path)
    resp = client.post(URL, json={"args": {"source": "a.csv"}})
    assert resp.status_code == 200
    assert resp.json() == {"stdout": '{"ok":1}', "stderr": "note", "exit_code": 0}
    assert sb.calls == [["../.tools/chart/launch", "summary", '{"source": "a.csv"}']]
    # Reading a view is reading the item.
    assert loc.verbs == ["read_content"]
    # The sandbox it wakes is created with what a turn would mount.
    assert reg.tools == [{"app-tool": "a" * 64}]
    # The item's variables, the same as a tool call gets.
    assert sb.envs[0]["API_KEY"] == "sk-1"


def test_a_non_zero_exit_is_an_answer(tmp_path):
    client, *_ = _client(tmp_path, sandbox=_Sandbox(ExecResult(exit_code=2, stderr=b"no column x")))
    resp = client.post(URL, json={"args": {}})
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 2 and resp.json()["stderr"] == "no column x"


def test_args_over_the_argv_limit_are_refused_before_anything_runs(tmp_path):
    client, sb, reg, _ = _client(tmp_path)
    resp = client.post(URL, json={"args": {"values": "x" * ARGV_MAX}})
    assert resp.status_code == 413
    assert "file path" in resp.json()["detail"]
    assert sb.calls == [] and reg.tools == []


def test_args_just_under_the_limit_run(tmp_path):
    client, sb, *_ = _client(tmp_path)
    overhead = len(json.dumps({"v": ""}))
    resp = client.post(URL, json={"args": {"v": "x" * (ARGV_MAX - overhead - 1)}})
    assert resp.status_code == 200


def test_unknown_plugin_is_404(tmp_path):
    client, *_ = _client(tmp_path)
    assert client.post("/a/pm/items/i1/view-plugins/nope/x", json={"args": {}}).status_code == 404


def test_a_plugin_with_no_sandbox_half_says_so(tmp_path):
    client, sb, *_ = _client(tmp_path, sheet=None)
    resp = client.post("/a/pm/items/i1/view-plugins/sheet/x", json={"args": {}})
    assert resp.status_code == 404
    assert "no sandbox commands" in resp.json()["detail"]
    assert sb.calls == []


def test_a_missing_launcher_is_a_loud_error_naming_the_plugin(tmp_path):
    """`kind: http` with a `{bundle}` plugin means "already in sandbox-host
    builtin/" — when it is not, the shell's `not found` must not reach the
    page as if the command itself had failed."""
    missing = ExecResult(exit_code=127, stderr=b"sh: ../.tools/chart/launch: not found")
    client, *_ = _client(tmp_path, sandbox=_Sandbox(missing))
    resp = client.post(URL, json={"args": {}})
    assert resp.status_code == 502
    assert "view plugin 'chart'" in resp.json()["detail"]
    assert "not installed" in resp.json()["detail"]


def test_an_artifact_plugin_needs_the_hosted_backend(tmp_path):
    client, sb, *_ = _client(tmp_path, chart={"artifact": "https://g/chart"})
    resp = client.post(URL, json={"args": {}})
    assert resp.status_code == 501
    assert "view plugin 'chart'" in resp.json()["detail"]
    assert sb.calls == []


def test_an_artifact_plugin_that_did_not_resolve_is_refused(tmp_path):
    client, sb, *_ = _client(
        tmp_path, sandbox=_Sandbox(resolves_tools=True), chart={"artifact": "https://g/chart"}
    )
    resp = client.post(URL, json={"args": {}})
    assert resp.status_code == 502
    assert "could not be resolved" in resp.json()["detail"]


def test_an_artifact_plugin_runs_when_resolved_and_mounted(tmp_path):
    ext = ExternalTools(shas={"chart": "c" * 64})
    client, sb, reg, _ = _client(
        tmp_path,
        sandbox=_Sandbox(resolves_tools=True),
        external=ext,
        chart={"artifact": "https://g/chart"},
    )
    assert client.post(URL, json={"args": {}}).status_code == 200
    assert reg.tools == [{"chart": "c" * 64}]


def test_a_live_sandbox_started_before_the_plugin_was_installed_says_so(tmp_path):
    ext = ExternalTools(shas={"chart": "c" * 64})
    live = _Registry(_Session(handle=SandboxHandle(id="h0"), tools={}))
    client, sb, *_ = _client(
        tmp_path,
        sandbox=_Sandbox(resolves_tools=True),
        registry=live,
        external=ext,
        chart={"artifact": "https://g/chart"},
    )
    resp = client.post(URL, json={"args": {}})
    assert resp.status_code == 409
    assert "started before" in resp.json()["detail"]
    assert sb.calls == []


def test_docker_backend_is_unsupported(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import workspace_app.api.view_plugin_routes as routes

    class _Docker(_Sandbox):
        pass

    monkeypatch.setattr(routes, "DockerSandbox", _Docker)
    client, sb, *_ = _client(tmp_path, sandbox=_Docker())
    resp = client.post(URL, json={"args": {}})
    assert resp.status_code == 501
    assert "docker" in resp.json()["detail"]
    assert sb.calls == []
