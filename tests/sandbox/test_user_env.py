"""Per-item user environment variables — the delivery half.

`write_user_env` puts the user's variables where the tool launchers can read
them: the sandbox's INFRA AREA, a sibling of the walked workspace, exactly where
`.ready` (#366) and `.home` (#393) live. That placement is what buys — for free
— never walked, never in the file tree, never mirrored to durable storage, not
charged against the workspace quota, and reaped with the sandbox.

It has to be a first-class op rather than an `upload`: `upload` / `upload_file` /
`download` are all workspace-root-relative and cannot address the infra area at
all. `mark_ready` / `is_ready` exist for the same reason.

No subprocess here, so these are unit tests — the exec-side half (the launcher
actually exporting them) has its own tests.
"""

import os
import stat
from pathlib import Path

import pytest

from workspace_app.sandbox.isolated_process import IsolatedProcessSandbox
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec

USER_ENV_FILE = ".userenv"


class TestMock:
    async def test_written_content_is_kept(self):
        sandbox = MockSandbox()
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "API_KEY=sk-1\n")
        assert sandbox.user_env(h) == "API_KEY=sk-1\n"

    async def test_a_fresh_sandbox_has_none(self):
        # `None`, not `""` — "nobody wrote one" and "every variable was deleted"
        # are different states, and only the second should reach the launchers.
        sandbox = MockSandbox()
        h = await sandbox.create(SandboxSpec())
        assert sandbox.user_env(h) is None

    async def test_it_never_appears_in_the_workspace(self):
        # The whole point of the infra area: the agent's file tools and the
        # mirror walk the workspace, and this is not in it.
        sandbox = MockSandbox()
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "API_KEY=sk-1\n")
        assert await sandbox.walk(h, "/") == []
        assert await sandbox.exists(h, f"/{USER_ENV_FILE}") is False

    async def test_a_rewrite_replaces_rather_than_appends(self):
        # Deleting a variable has to reach the tools. The file is rebuilt whole
        # from the item every turn, so an append-flavoured implementation would
        # keep resurrecting removed keys.
        sandbox = MockSandbox()
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "A=1\nB=2\n")
        await sandbox.write_user_env(h, "A=1\n")
        assert sandbox.user_env(h) == "A=1\n"

    async def test_an_unknown_handle_raises(self):
        sandbox = MockSandbox()
        with pytest.raises(SandboxNotFound):
            await sandbox.write_user_env(SandboxHandle(id="never"), "A=1\n")

    async def test_kill_takes_it_with_the_sandbox(self):
        sandbox = MockSandbox()
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "A=1\n")
        await sandbox.kill(h)
        h2 = await sandbox.create(SandboxSpec(), sandbox_id=h.id)
        assert sandbox.user_env(h2) is None


class TestLocalProcess:
    @pytest.fixture
    def sandbox(self, tmp_path) -> LocalProcessSandbox:
        return LocalProcessSandbox(root_dir=tmp_path, isolate=False)

    async def test_it_lands_beside_the_workspace_not_inside_it(
        self, sandbox: LocalProcessSandbox, tmp_path
    ):
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "API_KEY=sk-1\n")

        assert (tmp_path / h.id / USER_ENV_FILE).read_text() == "API_KEY=sk-1\n"
        # a SIBLING of `root/`, like `.ready` and `.home` — not in the workspace
        assert not (tmp_path / h.id / "root" / USER_ENV_FILE).exists()

    async def test_it_is_invisible_to_walk_and_exists(self, sandbox: LocalProcessSandbox):
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "API_KEY=sk-1\n")
        assert await sandbox.walk(h, "/") == []
        assert await sandbox.exists(h, f"/{USER_ENV_FILE}") is False

    async def test_a_rewrite_replaces_rather_than_appends(
        self, sandbox: LocalProcessSandbox, tmp_path
    ):
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "A=1\nB=2\n")
        await sandbox.write_user_env(h, "A=1\n")
        assert (tmp_path / h.id / USER_ENV_FILE).read_text() == "A=1\n"

    async def test_an_unknown_handle_raises(self, sandbox: LocalProcessSandbox):
        with pytest.raises(SandboxNotFound):
            await sandbox.write_user_env(SandboxHandle(id="never"), "A=1\n")

    async def test_kill_takes_it_with_the_sandbox(self, sandbox: LocalProcessSandbox, tmp_path):
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "A=1\n")
        await sandbox.kill(h)
        assert not (tmp_path / h.id).exists()

    async def test_the_quota_does_not_charge_for_it(self, sandbox: LocalProcessSandbox, tmp_path):
        # The workspace quota measures the WORKSPACE. Charging a user for a file
        # we put there, that they cannot see and cannot delete, is the shape of
        # #538 — and `_ensure_headroom` would then refuse writes over it.
        h = await sandbox.create(SandboxSpec())
        before = await sandbox.disk_usage(h)
        await sandbox.write_user_env(h, "A=" + ("x" * 5000) + "\n")
        assert await sandbox.disk_usage(h) == before

    async def test_it_is_not_world_readable(self, sandbox: LocalProcessSandbox, tmp_path):
        # API keys on a shared host. 0600 from the moment it exists — created
        # with the mode rather than chmod'ed afterwards, so there is no window.
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "API_KEY=sk-1\n")
        mode = (tmp_path / h.id / USER_ENV_FILE).stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


class TestExecNamesTheFile:
    """`SANDBOX_USER_ENV` is how the launcher finds the file, and it is set the
    same way `SANDBOX_HOME` is (#393): the chroot-relative spelling inside the
    jail, the absolute path outside it. Both branches, because they are separate
    code and only one of them is exercised by any given deployment."""

    async def test_unjailed_names_the_absolute_path(self, tmp_path):
        sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
        h = await sb.create(SandboxSpec())
        _argv, _cwd, env = sb._exec_argv(h, ["true"])
        assert env["SANDBOX_USER_ENV"] == str(tmp_path / h.id / USER_ENV_FILE)

    async def test_the_jail_names_the_same_file_chroot_relative(self, tmp_path):
        sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
        h = await sb.create(SandboxSpec())
        _argv, cwd, env = sb._exec_argv(h, ["true"])
        # `/.userenv` is the in-chroot spelling of `<root>/.userenv` — a sibling
        # of the `/root` workspace, exactly like `/.home`.
        assert env["SANDBOX_USER_ENV"] == f"/{USER_ENV_FILE}"
        assert Path(cwd) == tmp_path / h.id

    async def test_it_is_set_even_before_anything_was_written(self, tmp_path):
        # The launcher guards with `-f`, so naming a not-yet-written file is
        # harmless — and one unconditional assignment beats a branch that can
        # disagree with the launcher about when the variable exists.
        sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
        h = await sb.create(SandboxSpec())
        _argv, _cwd, env = sb._exec_argv(h, ["true"])
        assert "SANDBOX_USER_ENV" in env
        assert not (tmp_path / h.id / USER_ENV_FILE).exists()


class TestIsolated:
    """The uid-isolated backend drops `exec` to a per-item uid, so a file the
    app process wrote 0600 is unreadable by the launcher unless it is handed
    over — the same treatment `.home` already gets in `_provision` (#393)."""

    @pytest.fixture
    def isolated(self, tmp_path):
        chowns: list[tuple[Path, int]] = []
        sb = IsolatedProcessSandbox(
            root_dir=tmp_path / "sb",
            cgroup_root=tmp_path / "cg",
            uid_base=os.getuid(),
            uid_range=1,
            acl_runner=lambda _argv: None,
            chown_runner=lambda p, u: chowns.append((p, u)),
        )
        sb.chown_calls = chowns  # ty: ignore[unresolved-attribute]
        return sb

    async def test_the_file_is_handed_to_the_uid_exec_drops_to(self, isolated, tmp_path):
        h = await isolated.create(SandboxSpec())
        isolated.chown_calls.clear()  # ignore create-time provisioning
        await isolated.write_user_env(h, "API_KEY=sk-1\n")

        uid = os.getuid()  # uid_range=1 collapses the derived uid to our own
        assert (tmp_path / "sb" / h.id / USER_ENV_FILE, uid) in isolated.chown_calls


def _stub_tool_bundle(root: Path, tool: str = "demo") -> Path:
    """A bundle shaped exactly like a prebuilt one, with a stand-in for the two
    executables. `_LAUNCH` execs `<loader> <interpreter> <bundle>/.venv/bin/<tool>`,
    so the interpreter has to stay a real ELF (`/usr/bin/env`) and the "tool" is
    what reports the environment it was handed."""
    from workspace_app.tooling.prebuild import _LAUNCH

    (root / "python" / "bin").mkdir(parents=True)
    (root / "python" / "bin" / "python3.12").symlink_to("/usr/bin/env")
    (root / ".venv" / "bin").mkdir(parents=True)
    exe = root / ".venv" / "bin" / tool
    exe.write_text("#!/bin/sh\nexec env\n")
    exe.chmod(0o755)
    launch = root / "launch"
    launch.write_text(_LAUNCH.format(ver="3.12", tool=tool))
    launch.chmod(0o755)
    return launch


@pytest.mark.integration
class TestTheWholeChain:
    """File → `SANDBOX_USER_ENV` → launcher → the tool's `os.environ`, with real
    processes and no LLM.

    Each half is unit-tested on its own, and each would keep passing if the
    HALVES stopped agreeing: the sandbox could write `.userenv` while the exec
    path named something else, or the launcher could read a variable nothing
    ever set. This is the seam, and the seam is where "modified only one of the
    four `_exec_argv` copies" would show up.
    """

    async def test_a_tool_launched_in_the_sandbox_sees_the_users_variables(self, tmp_path):
        sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
        h = await sb.create(SandboxSpec())
        launch = _stub_tool_bundle(tmp_path / h.id / "root" / "bundle")

        await sb.write_user_env(h, "API_KEY=sk-1\nREGION=tw\n")
        result = await sb.exec(h, [str(launch)])

        out = result.stdout.decode()
        assert "API_KEY=sk-1" in out
        assert "REGION=tw" in out

    async def test_a_deleted_variable_stops_reaching_the_tool(self, tmp_path):
        sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
        h = await sb.create(SandboxSpec())
        launch = _stub_tool_bundle(tmp_path / h.id / "root" / "bundle")

        await sb.write_user_env(h, "API_KEY=sk-1\nGONE=yes\n")
        await sb.write_user_env(h, "API_KEY=sk-1\n")  # the next turn, one deleted
        out = (await sb.exec(h, [str(launch)])).stdout.decode()

        assert "API_KEY=sk-1" in out
        assert "GONE" not in out

    async def test_a_value_carrying_shell_syntax_arrives_intact(self, tmp_path):
        sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
        h = await sb.create(SandboxSpec())
        launch = _stub_tool_bundle(tmp_path / h.id / "root" / "bundle")

        tricky = "a=b c#d$e`f'g\"h$(echo no)"
        await sb.write_user_env(h, f"TOKEN={tricky}\n")
        out = (await sb.exec(h, [str(launch)])).stdout.decode()

        assert f"TOKEN={tricky}" in out
