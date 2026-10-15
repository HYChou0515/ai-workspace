"""A catalog hit leaves nothing in the stored thread
(`docs/plan-tool-on-demand-loading.md`, review round 1 A1).

The decision was the user's own: the load-and-retry pair must not be in the
conversation record. P5 only rewrote the OUTPUT, so the record still kept a
`role="tool"` message carrying the command's real name and the arguments the
model invented at the empty catalog schema — a call that never happened, with
a parameter that does not exist, replayed into every later turn.

The live card stays: `ToolStart` is emitted before the tool runs, so dropping
it would rest on a race. What must not survive is the stored message.
"""

from __future__ import annotations

from specstar import QB

from workspace_app.api import create_app
from workspace_app.api.events import RunDone, ToolEnd, ToolStart
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class _CatalogThenRealRunner:
    """One turn as it really goes: the model calls an unloaded command (the
    catalog answers with the schema), then calls it for real."""

    async def run(self, prompt, ctx):
        yield ToolStart(call_id="c1", name="data-fetch", args={"lot_number": "A42"})
        # What the catalog entry's handler does, through the real context API.
        ctx.mark_tool_loaded("data-fetch", call_id="c1")
        yield ToolEnd(call_id="c1", output="載入 `data-fetch` 的用法")
        yield ToolStart(call_id="c2", name="data-fetch", args={"lot": "A42"})
        yield ToolEnd(call_id="c2", output="lot A42: 3 steps")
        yield RunDone()


def _thread(spec, iid):
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    return [m for m in conv.messages if m.role == "tool"]


def test_only_the_real_call_is_stored():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_CatalogThenRealRunner(),
    )

    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "why drift?"})

    tools = _thread(spec, iid)
    assert [m.tool_call_id for m in tools] == ["c2"]
    assert tools[0].content == "lot A42: 3 steps"
    # The invented argument is nowhere in the record — a later turn replaying
    # this thread must not learn a parameter name the schema does not have.
    assert all("lot_number" not in str(m.tool_args or {}) for m in tools)


def test_a_turn_with_no_catalog_hit_stores_every_tool_message():
    """The control. A mutation that drops tool messages wholesale has to
    redden here, not just in the test above."""

    class _TwoRealCalls:
        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="read_file", args={"path": "a.md"})
            yield ToolEnd(call_id="c1", output="A")
            yield ToolStart(call_id="c2", name="read_file", args={"path": "b.md"})
            yield ToolEnd(call_id="c2", output="B")
            yield RunDone()

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_TwoRealCalls()
    )

    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "read both"})

    assert [m.tool_call_id for m in _thread(spec, iid)] == ["c1", "c2"]
