"""The agent's own `exec` cannot reach a third-party tool, and is told why
(docs/plan-tool-identity-secrets.md P2).

The host makes those bundles readable by the tool group only, so a shell command
that names one fails with the kernel's "Permission denied" — accurate, and no
help: it reads like a broken install, and the natural next move is to retry or
to chmod. The result says instead that the tool is used through its tool call.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, exec_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ExecResult
from workspace_app.tooling.registry import CommandInfo, PackageInfo

_ERP = PackageInfo(
    name="erp",
    install_dir="../.tools/erp",
    commands=(CommandInfo(name="lot-status", description="d", params_json_schema={}),),
    third_party=True,
)
_PLOT = PackageInfo(
    name="sci-plot",
    install_dir="../.tools/sci-plot",
    commands=(CommandInfo(name="chart", description="d", params_json_schema={}),),
)


class _Denying(MockSandbox):
    def __init__(self, result: ExecResult) -> None:
        super().__init__()
        self.result = result

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None, as_tool=None):
        return self.result


def _ctx(result: ExecResult) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            sandbox=_Denying(result),
            filestore=MemoryFileStore(),
            files=WorkspaceFiles(MemoryFileStore()),
            packages=[_ERP, _PLOT],
        )
    )


_DENIED = ExecResult(
    exit_code=1, stdout=b"", stderr=b"cat: ../.tools/erp/creds.py: Permission denied\n"
)


async def test_reading_a_third_party_tool_says_to_use_its_tool_call():
    text = await exec_impl(_ctx(_DENIED), ["cat", "../.tools/erp/creds.py"])
    assert "only through its tool call" in text
    assert "`erp__lot-status`" in text
    # The kernel's own words stay: the note explains them, it does not replace them.
    assert "Permission denied" in text


async def test_running_one_says_the_same():
    result = ExecResult(
        exit_code=126, stdout=b"", stderr=b"sh: 1: ../.tools/erp/launch: Permission denied\n"
    )
    text = await exec_impl(_ctx(result), ["sh", "-c", "../.tools/erp/launch lot-status {}"])
    assert "only through its tool call" in text


async def test_a_first_party_tool_gets_no_such_note():
    result = ExecResult(
        exit_code=1, stdout=b"", stderr=b"cat: ../.tools/sci-plot/x: Permission denied\n"
    )
    text = await exec_impl(_ctx(result), ["cat", "../.tools/sci-plot/x"])
    assert "only through its tool call" not in text


async def test_an_unrelated_failure_gets_no_such_note():
    result = ExecResult(exit_code=1, stdout=b"", stderr=b"cat: notes.md: No such file\n")
    text = await exec_impl(_ctx(result), ["cat", "notes.md"])
    assert "only through its tool call" not in text


async def test_a_success_that_mentions_the_path_gets_no_such_note():
    result = ExecResult(exit_code=0, stdout=b"../.tools/erp\n", stderr=b"")
    text = await exec_impl(_ctx(result), ["ls", "-d", "../.tools/erp"])
    assert "only through its tool call" not in text
