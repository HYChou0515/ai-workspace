"""`show_file` asks a plugin to validate its own view first (#847/#848 PR1 P9).

A `*.ai.yaml` whose `view:` belongs to a plugin that declares `validate` runs
the plugin's `validate` command in the item's sandbox before anything is shown:
a non-zero exit returns the error and DECLARES NOTHING (the rule for an
unresolvable path), a zero exit appends its one-line summary to the reply.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, show_file_impl
from workspace_app.agent.shown_files import SHOWN_FILES_MARKER
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.sandbox.protocol import ExecResult, Sandbox, SandboxHandle
from workspace_app.view_plugins import discover_view_plugins
from workspace_app.view_plugins.skills import register_for_agents


class _Sandbox:
    def __init__(self, result: ExecResult):
        self.result = result
        self.calls: list[list[str]] = []

    async def exists(self, handle, path: str) -> bool:
        return False

    async def exec(self, handle, cmd, on_output=None, env=None):
        self.calls.append(list(cmd))
        return self.result


def _plugin(root: Path, name: str, kind: str, *, validate: bool) -> None:
    d = root / name
    (d / "web").mkdir(parents=True)
    (d / "web" / "index.js").write_text("export {};\n")
    sandbox: dict = {"artifact": "https://g/x"}
    if validate:
        sandbox["validate"] = True
    manifest = {"name": name, "sdk": "1", "kinds": [kind], "sandbox": sandbox}
    (d / "plugin.json").write_text(json.dumps(manifest))


@pytest.fixture(autouse=True)
def plugins(tmp_path: Path):
    _plugin(tmp_path / "p", "chart", "chart", validate=True)
    _plugin(tmp_path / "p", "sketch", "sketch", validate=False)
    register_for_agents(discover_view_plugins(tmp_path / "p"))
    yield
    register_for_agents([])


async def _show(text: str, result: ExecResult, path: str = "/views/yield.ai.yaml"):
    files = WorkspaceFiles(MemoryFileStore())
    await files.write("inv-1", path, text.encode())
    sb = _Sandbox(result)
    ctx = AgentToolContext(
        investigation_id="inv-1",
        files=files,
        sandbox=cast("Sandbox", sb),
        handle=SandboxHandle(id="h"),
    )
    out = await show_file_impl(RunContextWrapper(ctx), path.lstrip("/"))
    return out, sb.calls


def _declared(out: str) -> list[dict]:
    _head, sep, payload = out.partition(SHOWN_FILES_MARKER)
    return json.loads(payload)["shown_files"] if sep else []


CHART = "view: chart\nsource: data/yield.csv\nmark: bar\n"


async def test_a_passing_validate_shows_the_view_and_says_what_it_found():
    ok = ExecResult(
        exit_code=0, stdout=b"highlight matches 3/25 groups; fail_rate 0.02-0.41\nmore\n"
    )
    out, calls = await _show(CHART, ok)
    assert calls == [["../.tools/chart/launch", "validate", '{"path": "views/yield.ai.yaml"}']]
    assert [s["path"] for s in _declared(out)] == ["/views/yield.ai.yaml"]
    assert "highlight matches 3/25 groups; fail_rate 0.02-0.41" in out
    assert "more" not in out.split(SHOWN_FILES_MARKER)[0]


async def test_a_failing_validate_shows_nothing_and_says_why():
    bad = ExecResult(exit_code=1, stdout=b"", stderr=b"source has no column fail_rate\n")
    out, _ = await _show(CHART, bad)
    assert _declared(out) == []
    assert out.startswith("error:")
    assert "source has no column fail_rate" in out
    assert "chart" in out


async def test_a_plugin_kind_without_validate_is_only_parsed():
    out, calls = await _show("view: sketch\ntitle: t\n", ExecResult(exit_code=0))
    assert calls == []
    assert len(_declared(out)) == 1


async def test_a_plugin_view_that_does_not_parse_shows_nothing():
    out, calls = await _show("view: sketch\ntitle: [unclosed\n", ExecResult(exit_code=0))
    assert calls == []
    assert _declared(out) == []
    assert "does not parse" in out


@pytest.mark.parametrize(
    "text", ["view: table\nentity: issue\n", "view: board\n  bad: [\n", "a: 1\n"]
)
async def test_builtin_kinds_and_other_yaml_are_unchanged(text: str):
    out, calls = await _show(text, ExecResult(exit_code=1))
    assert calls == []
    assert len(_declared(out)) == 1


async def test_other_files_never_run_anything():
    out, calls = await _show(CHART, ExecResult(exit_code=1), path="/views/yield.yaml")
    assert calls == []
    assert len(_declared(out)) == 1


# ── when the check cannot run, the view is shown and the reply says why ────
# Refusing there sent the agent round a fix loop on a correct file: the fault
# is the deployment's (no launcher, no tools on this backend), not the view's.


async def test_a_missing_launcher_shows_the_view_with_a_note():
    gone = ExecResult(exit_code=127, stderr=b"sh: 1: ../.tools/chart/launch: not found\n")
    out, calls = await _show(CHART, gone)
    assert len(calls) == 1
    assert len(_declared(out)) == 1
    head = out.split(SHOWN_FILES_MARKER)[0]
    assert "could not check" in head and "chart" in head
    assert not out.startswith("error:")


async def test_a_backend_with_no_tools_shows_the_view_with_a_note(monkeypatch):
    import workspace_app.agent.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_backend_has_no_tools", lambda sandbox: True)
    out, calls = await _show(CHART, ExecResult(exit_code=1))
    assert calls == []
    assert len(_declared(out)) == 1
    assert "could not check" in out.split(SHOWN_FILES_MARKER)[0]


async def test_a_plugin_shadowed_by_an_agent_tool_is_not_run():
    """An app tool of the same name owns `/.tools/chart`; running its
    `validate` would be running somebody else's command."""
    from workspace_app.tooling.registry import PackageInfo

    files = WorkspaceFiles(MemoryFileStore())
    await files.write("inv-1", "/views/yield.ai.yaml", CHART.encode())
    sb = _Sandbox(ExecResult(exit_code=1))
    ctx = AgentToolContext(
        investigation_id="inv-1",
        files=files,
        sandbox=cast("Sandbox", sb),
        handle=SandboxHandle(id="h"),
        packages=[PackageInfo(name="chart", install_dir="../.tools/chart", commands=())],
    )
    out = await show_file_impl(RunContextWrapper(ctx), "views/yield.ai.yaml")
    assert sb.calls == []
    assert len(_declared(out)) == 1
    assert "could not check" in out.split(SHOWN_FILES_MARKER)[0]


async def test_a_pathologically_nested_file_does_not_break_show_file():
    deep = "view: sketch\nx: " + "[" * 5000 + "]" * 5000 + "\n"
    out, calls = await _show(deep, ExecResult(exit_code=0))
    assert calls == []
    assert not out.startswith("error: Traceback")


async def test_a_quoted_view_line_in_an_unparseable_file_is_still_recognised():
    out, _ = await _show('view: "sketch"\ntitle: [unclosed\n', ExecResult(exit_code=0))
    assert _declared(out) == []
    assert "does not parse" in out


async def test_a_sandbox_that_fails_to_run_the_check_shows_the_view_with_a_note():
    class _Broken(_Sandbox):
        async def exec(self, handle, cmd, on_output=None, env=None):
            raise RuntimeError("sandbox host unreachable")

    files = WorkspaceFiles(MemoryFileStore())
    await files.write("inv-1", "/views/yield.ai.yaml", CHART.encode())
    ctx = AgentToolContext(
        investigation_id="inv-1",
        files=files,
        sandbox=cast("Sandbox", _Broken(ExecResult(exit_code=0))),
        handle=SandboxHandle(id="h"),
    )
    out = await show_file_impl(RunContextWrapper(ctx), "views/yield.ai.yaml")
    assert len(_declared(out)) == 1
    assert "could not check" in out.split(SHOWN_FILES_MARKER)[0]
