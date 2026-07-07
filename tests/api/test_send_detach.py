"""#493 symptom 1 (504): a message POST must not hang until its turn ends.

The send path awaits its own turn only up to `send_await_timeout`, then DETACHES
it to the engine's background worker and returns 202 — so a long agent turn can't
sit on the request until the ingress `proxy-read-timeout` fires a 504. Fast turns
(every scripted-runner test) still finish within the deadline and persist
synchronously, so their behaviour is unchanged (test_messages covers that side).

Determinism: the runner blocks on an `asyncio.Event`, so the turn CANNOT finish
until the test releases it. A 202 while the turn is still gated open therefore
proves the POST detached rather than waited — no timing assertion needed.
"""

from __future__ import annotations

import asyncio

from httpx import ASGITransport
from specstar import QB

from workspace_app.api import MessageDelta, RunDone, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient
from .conftest import register_rca_item


class _GatedRunner:
    """A runner whose turn blocks mid-flight until `gate` is set — so the turn is
    guaranteed to still be running when the send deadline elapses."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def run(self, content, ctx):
        yield MessageDelta(text="working")
        await self._gate.wait()  # never completes until the test releases
        yield RunDone()


def _conv_for(spec, iid: str) -> Conversation:
    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources(QB.all()):
        data = r.data
        assert isinstance(data, Conversation)
        if data.item_id == iid:
            return data
    raise AssertionError("no conversation for item")


async def test_a_long_turn_detaches_from_the_post_and_returns_202():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    gate = asyncio.Event()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_GatedRunner(gate),  # ty: ignore[invalid-argument-type]
        # A short deadline so the still-gated turn outlives it and detaches.
        send_await_timeout=0.05,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})

    # The turn is blocked on the gate → it cannot have finished. A 202 proves the
    # POST detached at the deadline instead of hanging until the turn ended.
    assert r.status_code == 202
    # ...and no assistant reply is persisted yet — only the user's own message.
    assert [m.role for m in _conv_for(spec, iid).messages] == ["user"]

    # Release + tear down the worker so nothing lingers past the test.
    gate.set()
    await app.state.turn_engine.forget(iid)
