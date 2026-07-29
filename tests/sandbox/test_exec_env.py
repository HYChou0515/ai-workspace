"""`exec` carries the caller's environment variables into the command.

The values a user sets on an item reach their tools this way. Asserting through
`exec` rather than the argv builder is the point: what matters is that the
command SEES the variable, and that stays true however the backend arranges it.
"""

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxSpec

pytestmark = pytest.mark.integration


@pytest.fixture
def sandbox(tmp_path) -> LocalProcessSandbox:
    return LocalProcessSandbox(root_dir=tmp_path, isolate=False)


async def test_a_variable_handed_to_exec_reaches_the_command(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["sh", "-c", "echo $GREETING"], env={"GREETING": "hi"})
    assert r.stdout == b"hi\n"


async def test_the_caller_wins_over_a_name_the_exec_path_sets_itself(
    sandbox: LocalProcessSandbox,
):
    """`HOME` is set by the exec path (#393, the per-sandbox `.home`). A caller
    that names it anyway must win: the decision recorded with the feature is
    that reserved names pass THROUGH, and the alternative — silently keeping
    ours — is "stored, listed, no effect"."""
    h = await sandbox.create(SandboxSpec())
    ours = await sandbox.exec(h, ["sh", "-c", "echo $HOME"])
    assert ours.stdout.strip() not in (b"", b"/somewhere-else")

    r = await sandbox.exec(h, ["sh", "-c", "echo $HOME"], env={"HOME": "/somewhere-else"})
    assert r.stdout == b"/somewhere-else\n"


class TestEveryBackendAcceptsIt:
    """A double that cannot take `env` would let a caller pass one and assert
    nothing — the shape of failure #492 was about. So the doubles carry it too,
    and record it where a test can see what the caller asked for."""

    async def test_the_mock_records_what_the_caller_asked_for(self):
        from workspace_app.sandbox.mock import MockSandbox

        sb = MockSandbox()
        h = await sb.create(SandboxSpec())
        await sb.exec(h, ["true"], env={"API_KEY": "sk-1"})
        assert sb.exec_envs[-1] == {"API_KEY": "sk-1"}
