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
from workspace_app.sandbox.protocol import ExecResult, SandboxSpec
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

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None, tool=False):
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


async def test_an_artifact_view_plugin_gets_the_note_too():
    """Review round 1 (F2): an `{artifact: url}` view plugin is mounted from the
    third-party store and hidden the same way, but is not in `packages`."""
    actx = AgentToolContext(
        investigation_id="inv-1",
        sandbox=_Denying(
            ExecResult(
                exit_code=1, stdout=b"", stderr=b"cat: ../.tools/wafermap/x: Permission denied\n"
            )
        ),
        filestore=MemoryFileStore(),
        files=WorkspaceFiles(MemoryFileStore()),
        sandbox_spec=SandboxSpec(tools={"wafermap": "a" * 64}),
    )
    text = await exec_impl(RunContextWrapper(actx), ["cat", "../.tools/wafermap/x"])
    assert "only through its tool call" in text


async def test_a_tool_whose_name_merely_starts_the_same_gets_no_note():
    """Review round 1: `.tools/erpnext` is not `erp`."""
    result = ExecResult(
        exit_code=1, stdout=b"", stderr=b"cat: ../.tools/erpnext/x: Permission denied\n"
    )
    text = await exec_impl(_ctx(result), ["cat", "../.tools/erpnext/x"])
    assert "only through its tool call" not in text


async def test_a_failure_that_was_not_refused_gets_no_such_note():
    """The tool's path in a failure that is NOT "Permission denied" — a typo'd
    command name, say — is not the platform hiding anything."""
    result = ExecResult(exit_code=1, stdout=b"", stderr=b"cat: ../.tools/erp/nope: No such file\n")
    text = await exec_impl(_ctx(result), ["cat", "../.tools/erp/nope"])
    assert "only through its tool call" not in text
