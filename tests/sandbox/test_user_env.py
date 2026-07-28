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
        # Not "" by accident — a sandbox nobody wrote to must be distinguishable
        # from one whose variables were all deleted, so the exec path can skip
        # setting SANDBOX_USER_ENV rather than point it at a phantom.
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

    async def test_it_is_not_world_readable(self, sandbox: LocalProcessSandbox, tmp_path):
        # API keys on a shared host. 0600 from the moment it exists — created
        # with the mode rather than chmod'ed afterwards, so there is no window.
        h = await sandbox.create(SandboxSpec())
        await sandbox.write_user_env(h, "API_KEY=sk-1\n")
        mode = (tmp_path / h.id / USER_ENV_FILE).stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


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
