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


def _tool_output(call_id: str, text: str):
    """The `tool_output` stream event shape the runner's `_map_event` reads."""
    from dataclasses import dataclass

    @dataclass
    class _Raw:
        call_id: str

    @dataclass
    class _Item:
        raw_item: object
        output: object

    @dataclass
    class _Ev:
        type: str
        name: str
        item: object

    return _Ev(
        type="run_item_stream_event",
        name="tool_output",
        item=_Item(raw_item=_Raw(call_id=call_id), output=text),
    )


def _catalog_end(ctx, call_id: str, schema_text: str):
    """What the real runner emits for a catalog hit: the output mapped through
    `_map_event`, which rewrites it to one line and records the id as
    ephemeral. Routed through the REAL mapper so the chain under test is the
    production one — a hand-made `ToolEnd` would skip the half that decides
    the message is not stored."""
    from workspace_app.api.litellm_runner import _map_event

    return _map_event(_tool_output(call_id, schema_text), ctx)


class _CatalogThenRealRunner:
    """One turn as it really goes: the model calls an unloaded command (the
    catalog answers with the schema), then calls it for real."""

    async def run(self, prompt, ctx):
        yield ToolStart(call_id="c1", name="data-fetch", args={"lot_number": "A42"})
        # What the catalog entry's handler does, through the real context API.
        ctx.mark_tool_loaded("data-fetch", call_id="c1")
        yield _catalog_end(ctx, "c1", '{"properties": {"lot": {"type": "string"}}}')
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


def test_a_reused_call_id_does_not_erase_the_turns_real_tool_messages():
    """An OpenAI-compatible endpoint passes the server's `tool_call.id`
    through verbatim, and small models do repeat them. Matching the catalog's
    record on the id ALONE silenced every later tool message of the turn — a
    `write_file` that really ran included (verify round F2)."""

    class _ReusedId:
        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="data-fetch", args={"lot_number": "A42"})
            ctx.mark_tool_loaded("data-fetch", call_id="c1")
            yield _catalog_end(ctx, "c1", '{"properties": {"lot": {"type": "string"}}}')
            # The same id, now a real call of the same tool…
            yield ToolStart(call_id="c1", name="data-fetch", args={"lot": "A42"})
            yield ToolEnd(call_id="c1", output="lot A42: 3 steps")
            # …and a write that really happened.
            yield ToolStart(call_id="c1", name="write_file", args={"path": "r.md"})
            yield ToolEnd(call_id="c1", output="written 42 bytes")
            yield RunDone()

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_ReusedId()
    )

    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "go"})

    stored = [(m.tool_name, m.content) for m in _thread(spec, iid)]
    assert ("write_file", "written 42 bytes") in stored, stored
    assert ("data-fetch", "lot A42: 3 steps") in stored, stored
    # …and the catalog's own answer still did not survive.
    assert all("的用法" not in c for _n, c in stored), stored
