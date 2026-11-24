"""docs/plan-archive-full.md P3: an item whose write-back the archive refused
takes no new turn until a write-back lands — and says why, in rsync's words.

The incident: the archive filled, every write-back failed silently, people kept
working, and a rollout of the sandbox host erased everything since. Walked
through the real app: `create_app`'s registry records the refusal (P2), and the
same turn gate both the chat send and a workflow step pass refuses with 507."""

from __future__ import annotations

from typing import Any

from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ArchiveWriteFailed, SandboxHandle

from ._client import TestClient
from .conftest import register_rca_item

_ENOSPC = (
    'rsync: [receiver] write failed on "/a/f": No space left on device (28)\n'
    "rsync error: error in file IO (code 11) at receiver.c(657) [receiver=3.5.0]"
)


class _ArchiveSandbox(MockSandbox):
    """A host-managed sandbox whose archive can be made to refuse write-backs."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse = ""

    async def persist(self, handle: SandboxHandle, *, delete: bool, pack: bool = False) -> None:
        if self.refuse:
            raise ArchiveWriteFailed(self.refuse)


class _Runner:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run(self, prompt: str, ctx: Any):
        self.ran.append(prompt)
        yield RunDone()


def _app(sandbox: _ArchiveSandbox, runner: _Runner):
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=MemoryFileStore(),
        runner=runner,
        host_managed_durable=True,
    )
    return app, spec, iid


def _write_back(client: TestClient, iid: str) -> None:
    """A real write-back through a real entry: the terminal's command route wakes
    the sandbox, runs the command, and writes the item back to the archive."""
    assert client.post(f"/a/rca/items/{iid}/exec", json={"cmd": ["echo", "hi"]}).status_code == 200


def test_a_refused_write_back_stops_new_turns_with_the_reason():
    sandbox, runner = _ArchiveSandbox(), _Runner()
    app, _spec, iid = _app(sandbox, runner)
    with TestClient(app) as client:
        sandbox.refuse = _ENOSPC
        _write_back(client, iid)

        resp = client.post(f"/a/rca/items/{iid}/messages", json={"content": "keep going"})

        assert resp.status_code == 507
        assert resp.json()["detail"] == {"error": "archive_write_failed", "reason": _ENOSPC}
        assert runner.ran == []
        for c in client.get(f"/a/rca/items/{iid}/chats").json():
            export = client.get(f"/a/rca/items/{iid}/chats/{c['chat_id']}/export-chat")
            assert "keep going" not in export.text


def test_a_write_back_that_lands_lets_turns_through_again():
    sandbox, runner = _ArchiveSandbox(), _Runner()
    app, _spec, iid = _app(sandbox, runner)
    with TestClient(app) as client:
        sandbox.refuse = _ENOSPC
        _write_back(client, iid)
        assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "a"}).status_code == 507

        sandbox.refuse = ""
        _write_back(client, iid)

        assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "b"}).status_code == 202
        assert runner.ran[-1].endswith("b")


def test_only_the_refused_item_is_stopped():
    sandbox, runner = _ArchiveSandbox(), _Runner()
    app, spec, iid = _app(sandbox, runner)
    other = register_rca_item(spec)
    with TestClient(app) as client:
        sandbox.refuse = _ENOSPC
        _write_back(client, iid)
        sandbox.refuse = ""

        assert (
            client.post(f"/a/rca/items/{other}/messages", json={"content": "x"}).status_code == 202
        )
