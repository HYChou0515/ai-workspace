import asyncio
import base64

from specstar import QB

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.config.catalog_build import build_catalog
from workspace_app.config.loader import load
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient, TestClient
from .conftest import Harness, register_rca_item

# A real 1×1 PNG — libmagic sniffs it as image/png.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _vision_catalog(tmp_path):
    """A catalog where the rca-picker preset `claude-opus` is flagged vision=True,
    so attaching it makes the resolved workspace agent a VLM."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "agents:\n"
        "  presets:\n"
        "    claude-opus:\n"
        '      model: "claude-opus-4-7"\n'
        "      vision: true\n",
        encoding="utf-8",
    )
    return build_catalog(load(config_path=cfg, env={}), config_dir=tmp_path)


def test_vision_model_inlines_attached_images_into_the_turn(tmp_path):
    """Source A end-to-end: a VLM main model (`Preset.vision`) receives composer-
    attached images inline as image parts on the turn context, so it sees the
    pixels directly — no `read_image` round-trip through the separate VLM."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec, attached_preset="claude-opus")
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt, ctx):
            captured["urls"] = list(ctx.turn_image_urls)
            yield RunDone()

    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Capture(),
        agent_config_catalog=_vision_catalog(tmp_path),
    )
    client = TestClient(app)
    client.put(f"/a/rca/items/{iid}/files/shot.png", content=_PNG)

    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "what defect?", "image_paths": ["/shot.png"]},
    )

    urls = captured["urls"]
    assert isinstance(urls, list)
    assert urls == [f"data:image/png;base64,{base64.b64encode(_PNG).decode('ascii')}"]


def test_text_only_model_ignores_image_paths(tmp_path):
    """A text-only main model leaves the inline-image channel empty even when the
    composer sends `image_paths` — it reaches the image through `read_image` (and
    the `Attached \\`path\\`` note in the message), exactly as before."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)  # default preset (qwen3-local) is vision=False
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt, ctx):
            captured["urls"] = list(ctx.turn_image_urls)
            yield RunDone()

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Capture()
    )
    client = TestClient(app)
    client.put(f"/a/rca/items/{iid}/files/shot.png", content=_PNG)

    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "what defect?", "image_paths": ["/shot.png"]},
    )

    assert captured["urls"] == []


def test_reasoning_delta_persists_to_reasoning_channel():
    """A <think> reasoning delta is stored on the assistant message's
    reasoning field, separate from the visible answer."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = ScriptedAgentRunner(
        [
            MessageDelta(text="weighing the options", reasoning=True),
            MessageDelta(text="The answer is 42."),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    assistant = next(m for m in conv.messages if m.role == "assistant")
    assert assistant.content == "The answer is 42."
    assert assistant.reasoning == "weighing the options"


def test_profile_config_drives_the_turn_so_its_tools_are_allowed():
    """Creating an item from a profile that ships a `_config.json` (tool-demo)
    makes that profile's allowed_tools drive the turn — resolved through the
    AppCatalog (app ◇ profile ◇ preset)."""
    spec = make_spec(default_user="u")
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt, ctx):
            captured["allowed"] = ctx.agent_config.allowed_tools if ctx.agent_config else None
            yield RunDone()

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Capture()
    )
    client = TestClient(app)
    # Public so the default request user (not the item's created_by) may converse —
    # this test exercises profile→tools resolution, not access control (that's in
    # test_item_perm.py); the new private default would otherwise 404 the send.
    item_id = client.post(
        "/a/rca/items",
        json={"title": "t", "profile": "tool-demo", "permission": {"visibility": "public"}},
    ).json()["resource_id"]
    client.post(f"/a/rca/items/{item_id}/messages", json={"content": "q"})

    allowed = captured["allowed"]
    assert isinstance(allowed, list)
    assert "data-fetch" in allowed
    assert "csv-column-summary" in allowed


def test_message_reasoning_effort_threads_to_the_turn_context():
    """The per-message reasoning_effort from the UI selector reaches the turn's
    ctx (→ the model's ModelSettings); absent → None (model default)."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt, ctx):
            captured["effort"] = ctx.reasoning_effort
            yield RunDone()

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Capture()
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q", "reasoning_effort": "high"})
    assert captured["effort"] == "high"
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    assert captured["effort"] is None


def test_ask_knowledge_base_tool_message_persists_with_citations():
    """When the agent's `ask_knowledge_base` tool runs, the persisted RCA
    tool message carries the KB sub-agent's resolved citations. Symmetric
    with `KbMessage.citations`; lets the FE render reference cards in RCA
    chat (same UX as direct KB chat)."""
    from workspace_app.api import ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    cite = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-a",
        filename="reflow-spec.md",
        start=0,
        end=50,
        source_chunk_ids=["ck-a"],
    )

    class _AskKbRunner:
        """Models a real turn: emits the ask_knowledge_base tool events AND
        mutates ctx the way the real impl does (stash citations on
        ctx.ask_kb_citations), so the persist path can be exercised end to
        end without spinning the full LLM/KB stack."""

        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": prompt})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([cite])
            yield ToolEnd(call_id="c1", output="answer with [1]")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_AskKbRunner()
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "why drift?"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool = next(m for m in conv.messages if m.role == "tool")
    assert tool.tool_name == "ask_knowledge_base"
    # The KB citations the bridge resolved show up on the tool message so a
    # reload (FE refetches the conversation on `done`) shows the cards.
    assert len(tool.citations) == 1
    assert tool.citations[0].marker == 1
    assert tool.citations[0].filename == "reflow-spec.md"


def test_ask_kb_tool_message_beyond_its_citation_pool_gets_empty_citations():
    """#54 defensive branch: when an `ask_knowledge_base` tool NAME recurs more
    times than its citation pool has buckets, the extra tool messages keep
    `citations=[]` while the per-name cursor still advances — exercising the
    `idx >= len(pool)` path of the per-name citation pairing."""
    from workspace_app.api import ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    cite = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-a",
        filename="reflow-spec.md",
        start=0,
        end=50,
        source_chunk_ids=["ck-a"],
    )

    class _TwoAskKbOneBucketRunner:
        """Two ask_knowledge_base calls but only ONE stashed citation bucket, so
        the second call runs past the end of the pool."""

        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": prompt})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([cite])
            yield ToolEnd(call_id="c1", output="answer with [1]")
            # Second call — no new bucket appended → the pool is exhausted.
            yield ToolStart(call_id="c2", name="ask_knowledge_base", args={"question": "again"})
            yield ToolEnd(call_id="c2", output="answer, no citations left")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_TwoAskKbOneBucketRunner(),
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "why drift?"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tools = [m for m in conv.messages if m.role == "tool" and m.tool_name == "ask_knowledge_base"]
    assert len(tools) == 2
    assert len(tools[0].citations) == 1  # first pairs with the only bucket
    assert tools[1].citations == []  # second is beyond the pool → stays empty


def test_infer_modules_tool_message_persists_with_citations():
    """Same shape as ask_knowledge_base: the `infer_modules` sub-agent
    bridge returns text + KB citations, persist pairs the Nth list with
    the Nth `infer_modules` tool message. Locks the parallel wiring so
    a future refactor of one citation pool doesn't silently demote the
    other."""
    from workspace_app.api import ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    cite = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-taxonomy",
        filename="modules.md",
        start=0,
        end=42,
        source_chunk_ids=["ck-m"],
    )

    class _InferRunner:
        async def run(self, prompt, ctx):
            yield ToolStart(
                call_id="c1",
                name="infer_modules",
                args={"path": "wafer-history.csv"},
            )
            ctx.subagent_citations.setdefault("infer_modules", []).append([cite])
            yield ToolEnd(
                call_id="c1", output="Classified 1 steps → wrote step2-data/module-map.csv."
            )
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_InferRunner()
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "classify these"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool = next(m for m in conv.messages if m.role == "tool")
    assert tool.tool_name == "infer_modules"
    assert len(tool.citations) == 1
    assert tool.citations[0].filename == "modules.md"


def test_infer_modules_citations_bubble_onto_outer_assistant_message():
    """When the outer assistant message references `[N]` after an
    `infer_modules` call, the bubble step pulls citations from the
    same pool as ask_knowledge_base — so a marker quoted by the answer
    finds its card regardless of which sub-agent surfaced it. Locks the
    shared `seen_subagent` pool design in persist()."""
    from workspace_app.api import MessageDelta, ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    cite = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-taxonomy",
        filename="modules.md",
        start=0,
        end=42,
        source_chunk_ids=["ck-m"],
    )

    class _InferThenSpeakRunner:
        async def run(self, prompt, ctx):
            yield ToolStart(
                call_id="c1",
                name="infer_modules",
                args={"path": "wafer-history.csv"},
            )
            ctx.subagent_citations.setdefault("infer_modules", []).append([cite])
            yield ToolEnd(call_id="c1", output="ok")
            yield MessageDelta(text="Per [1], STI covers the pad oxide.")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_InferThenSpeakRunner(),
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "?"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    asst = next(m for m in conv.messages if m.role == "assistant")
    assert [c.marker for c in asst.citations] == [1]
    assert asst.citations[0].filename == "modules.md"


def test_ask_kb_tool_end_without_a_citation_slot_persists_with_empty_citations():
    """Defensive: if an ask_knowledge_base ToolEnd reaches persist without
    a matching `ask_kb_citations` slot (e.g. a runner that emitted the
    events but never invoked the impl, or an impl that errored before
    appending), the persisted message keeps `citations=[]` — the
    `ask_kb_idx` cursor still advances so a later, paired call lines
    up correctly."""
    from workspace_app.api import ToolEnd, ToolStart

    class _OrphanAskKbRunner:
        async def run(self, prompt, ctx):
            # Note: NO append to ctx.ask_kb_citations — models the broken-
            # contract / pre-impl-error case.
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": "x"})
            yield ToolEnd(call_id="c1", output="no citations")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_OrphanAskKbRunner(),
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool = next(m for m in conv.messages if m.role == "tool")
    assert tool.tool_name == "ask_knowledge_base"
    assert tool.citations == []


def test_non_ask_kb_tool_message_does_not_get_citations_attached():
    """The persist step pairs `ask_kb_citations` ONLY with ask_knowledge_base
    tool messages. A different tool (`exec`, …) keeps `citations=[]`, even
    when another ask_kb earlier in the turn populated `ask_kb_citations`."""
    from workspace_app.api import ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    cite = Citation(
        marker=1,
        collection_id="c",
        document_id="d",
        filename="f.md",
        start=0,
        end=10,
        source_chunk_ids=["x"],
    )

    class _MixedRunner:
        async def run(self, prompt, ctx):
            # An ask_kb earlier in the turn — populates ctx, but this shouldn't
            # leak onto the exec tool message that follows.
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": "x"})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([cite])
            yield ToolEnd(call_id="c1", output="a")
            yield ToolStart(call_id="c2", name="exec", args={"cmd": ["echo", "y"]})
            yield ToolEnd(call_id="c2", output="ok")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_MixedRunner()
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool_msgs = [m for m in conv.messages if m.role == "tool"]
    kb_msg = next(m for m in tool_msgs if m.tool_name == "ask_knowledge_base")
    exec_msg = next(m for m in tool_msgs if m.tool_name == "exec")
    assert len(kb_msg.citations) == 1
    assert exec_msg.citations == []


def test_ask_kb_citations_bubble_onto_outer_assistant_message():
    """The KB sub-agent runs through `ask_knowledge_base`, which stashes the
    resolved citations on `ctx.ask_kb_citations`. When the outer RCA
    assistant message then quotes `[1]` / `[2]` in its prose, those
    matching citations must also land on THAT assistant message — not just
    on the tool message — so the chat UI renders reference cards under the
    answer the user actually reads.
    """
    from workspace_app.api import MessageDelta, ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    c1 = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-a",
        filename="a.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-a"],
    )
    c2 = Citation(
        marker=2,
        collection_id="col",
        document_id="doc-b",
        filename="b.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-b"],
    )
    c3_unused = Citation(
        marker=3,
        collection_id="col",
        document_id="doc-c",
        filename="c.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-c"],
    )

    class _AskKbThenAnswerRunner:
        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": prompt})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([c1, c2, c3_unused])
            yield ToolEnd(call_id="c1", output="kb said stuff")
            # The outer assistant references markers [1] and [2]; the third
            # ask_kb result (marker 3) was returned by the bridge but the
            # answer didn't end up citing it — so its card mustn't appear.
            yield MessageDelta(text="Based on [1] and [2].")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_AskKbThenAnswerRunner(),
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    asst = next(m for m in conv.messages if m.role == "assistant")
    # [1] and [2] match — bubbled onto the answer; [3] not quoted, stays off.
    assert [c.marker for c in asst.citations] == [1, 2]
    assert {c.filename for c in asst.citations} == {"a.md", "b.md"}


def test_outer_assistant_without_markers_inherits_latest_ask_kb_citations():
    """When the agent runs an ask_knowledge_base call and then produces an
    assistant message WITHOUT explicit `[N]` quotes — the common case
    where the agent forwards the KB result into a file via `write_file`
    or summarises in prose without re-numbering — the outer message
    still gets the LATEST ask_kb's citations bubbled onto it. Without
    this, the chat UI would render the answer citation-less even though
    every claim came from the KB.

    Deduped by chunk so the same passage cited twice collapses to one
    card. Sorted by marker so the rendered list reads `[1] [2] [3] …`.
    """
    from workspace_app.api import MessageDelta, ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    c1 = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-a",
        filename="a.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-a"],
    )
    c2 = Citation(
        marker=2,
        collection_id="col",
        document_id="doc-b",
        filename="b.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-b"],
    )

    class _RunnerNoQuote:
        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": prompt})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([c1, c2])
            yield ToolEnd(call_id="c1", output="kb answered with two refs")
            # The agent's prose forwards the result without quoting [1] / [2].
            yield MessageDelta(text="Forwarded the KB result into the report.")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_RunnerNoQuote()
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    asst = next(m for m in conv.messages if m.role == "assistant")
    # Both citations from the most-recent ask_kb call ride along — the chat
    # UI now shows the cards under the outer answer, not just under the
    # tool message.
    assert [c.marker for c in asst.citations] == [1, 2]
    assert {c.filename for c in asst.citations} == {"a.md", "b.md"}


def test_outer_assistant_without_markers_and_no_ask_kb_stays_citationless():
    """Symmetric defensive case: if NO ask_kb ran in this turn, the
    fallback is empty — the assistant message doesn't get citations
    smeared onto it from prior turns."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = ScriptedAgentRunner(
        [
            MessageDelta(text="Just thinking out loud, no KB consulted."),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    asst = next(m for m in conv.messages if m.role == "assistant")
    assert asst.citations == []


def test_marker_collision_across_two_ask_kb_calls_latest_wins():
    """If two ask_kb calls in the same turn both have a `[1]`, the assistant
    message's `[1]` quote resolves to the MOST RECENT call's citation —
    that's what the assistant was most recently looking at."""
    from workspace_app.api import MessageDelta, ToolEnd, ToolStart
    from workspace_app.resources.conversation import Citation

    first = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-old",
        filename="old.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-old"],
    )
    second = Citation(
        marker=1,
        collection_id="col",
        document_id="doc-new",
        filename="new.md",
        start=0,
        end=10,
        source_chunk_ids=["ck-new"],
    )

    class _TwoAskKbRunner:
        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="ask_knowledge_base", args={"question": "first"})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([first])
            yield ToolEnd(call_id="c1", output="first answer")
            yield ToolStart(call_id="c2", name="ask_knowledge_base", args={"question": "second"})
            ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([second])
            yield ToolEnd(call_id="c2", output="second answer")
            yield MessageDelta(text="Per [1], …")
            yield RunDone()

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_TwoAskKbRunner()
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    asst = next(m for m in conv.messages if m.role == "assistant")
    assert len(asst.citations) == 1
    assert asst.citations[0].filename == "new.md"  # most recent call wins


def test_tool_end_without_a_matching_start_persists_with_null_name_args():
    """Defensive: a ToolEnd with no preceding ToolStart still persists (name +
    args null), rather than crashing the turn."""
    from workspace_app.api import ToolEnd

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    runner = ScriptedAgentRunner([ToolEnd(call_id="orphan", output="out"), RunDone()])
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool = next(m for m in conv.messages if m.role == "tool")
    assert tool.content == "out"
    assert tool.tool_name is None and tool.tool_args is None
    assert isinstance(tool.created_at, int)


def test_export_returns_the_full_conversation_as_a_json_download(harness: Harness):
    harness.client.post(harness.wpath("/messages"), json={"content": "hello"})

    r = harness.client.get(harness.wpath("/export"))
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and harness.iid in cd  # downloads with a stamped filename

    data = r.json()
    assert data["investigation"]["id"] == harness.iid
    assert isinstance(data["exported_at"], int)
    roles = [m["role"] for m in data["messages"]]
    assert roles[0] == "user"
    # full agent detail is in the export: the tool call + the assistant answer
    assert any(m["role"] == "tool" for m in data["messages"])
    assert any(m["role"] == "assistant" and m["content"] for m in data["messages"])


def test_export_of_item_without_a_conversation_is_empty(harness: Harness):
    # An existing item never chatted in → empty export, read-only (no conversation created).
    r = harness.client.get(harness.wpath("/export"))
    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_export_unknown_item_404s(harness: Harness):
    # #95: workspace routes validate slug→item; an id not in the App 404s.
    r = harness.client.get("/a/rca/items/never-touched/export")
    assert r.status_code == 404


def test_export_carries_item_metadata_when_the_resource_exists():
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    client = TestClient(app)
    item_id = client.post(
        "/a/rca/items",
        json={"title": "Reflow voids", "severity": "P1", "product": "PanelX"},
    ).json()["resource_id"]

    meta = client.get(f"/a/rca/items/{item_id}/export").json()["investigation"]
    assert meta["title"] == "Reflow voids" and meta["owner"] == "default-user"  # owner from auth
    assert meta["severity"] == "P1" and meta["product"] == "PanelX"


def test_agent_metrics_without_an_assistant_message_attaches_to_nothing():
    from workspace_app.api import AgentMetrics, ToolEnd

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    runner = ScriptedAgentRunner(
        [
            ToolEnd(call_id="t", output="o"),  # a tool message, but no assistant answer
            AgentMetrics(phase="final", prompt_tokens=1, completion_tokens=2, elapsed_ms=3),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool = next(m for m in conv.messages if m.role == "tool")
    assert tool.metrics is None  # metrics never pin onto a non-assistant message


def test_post_message_is_accepted_and_queued(harness: Harness):
    """#43: POST queues the turn and returns 202 (the live SSE moved to the
    shared GET .../stream); it no longer streams the turn back inline."""
    response = harness.client.post(harness.wpath("/messages"), json={"content": "hello"})
    assert response.status_code == 202


async def test_post_broadcasts_the_user_message_and_turn_events_on_the_stream():
    """#43: posting to a shared investigation broadcasts the human's message
    (with its author) AND the triggered turn's events on the per-investigation
    stream every viewer subscribes to. (The engine stream is read directly —
    the HTTP SSE transport just wraps it — because ASGITransport can't read an
    infinite response incrementally.)"""
    from httpx import ASGITransport

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        get_user_id=lambda: "alice",
    )
    sub = app.state.turn_engine.subscribe(iid)  # register the subscriber first
    seen: list = []

    async def collect():
        async for ev in sub:
            seen.append(ev)
            if getattr(ev, "type", None) == "done":
                return

    collector = asyncio.create_task(collect())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post(f"/a/rca/items/{iid}/messages", json={"content": "yo"})
    await asyncio.wait_for(collector, 3)

    names = [type(e).__name__ for e in seen]
    assert "UserMessage" in names and "MessageDelta" in names
    um = next(e for e in seen if type(e).__name__ == "UserMessage")
    assert um.author == "alice" and um.content == "yo"


def test_post_message_appends_to_conversation(harness: Harness):
    # Message a SECOND item first so the conversation-lookup loop has to skip a
    # non-matching entry — exercises the false branch of the inner if.
    other = register_rca_item(harness.spec)
    harness.client.post(f"/a/rca/items/{other}/messages", json={"content": "ignored"})
    harness.client.post(harness.wpath("/messages"), json={"content": "first"})
    rm = harness.spec.get_resource_manager(Conversation)
    convs: list[Conversation] = []
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, Conversation)
        if data.item_id == harness.iid:
            convs.append(data)
    assert len(convs) == 1
    roles = [(m.role, m.content) for m in convs[0].messages]
    assert roles[0] == ("user", "first")
    # the scripted reply + tool output persist too, so re-entering the
    # workspace restores the full turn, not just the user's message.
    assert ("tool", "exit_code=0\n--- stdout ---\nhi") in roles
    assert ("assistant", "Done. The file printed 'hi'.") in roles


def test_turn_persists_timestamps_and_tool_call_detail(harness: Harness):
    """A reloaded log must restore the full detail: every message carries a
    `created_at`, and the tool message keeps the tool's name + args (captured
    from ToolStart), not just its output."""
    harness.client.post(harness.wpath("/messages"), json={"content": "hi"})
    rm = harness.spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == harness.iid
    )
    assert conv.messages, "expected a persisted turn"
    assert all(isinstance(m.created_at, int) and m.created_at > 0 for m in conv.messages)
    tool = next(m for m in conv.messages if m.role == "tool")
    assert tool.tool_name == "exec"
    assert tool.tool_args == {"cmd": ["echo", "hi"]}


def test_assistant_reply_persists_for_reload(harness: Harness):
    """The streamed assistant text is concatenated into one persisted
    assistant message (so a re-entry shows the agent's reply)."""
    harness.client.post(harness.wpath("/messages"), json={"content": "hi"})
    rm = harness.spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == harness.iid
    )
    assistant = [m for m in conv.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "Done. The file printed 'hi'."


def test_spa_index_served_at_root_when_dist_exists():
    """If web/dist has been built, GET / returns the React app's index.html."""
    from pathlib import Path

    spa_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if not (spa_dist / "index.html").is_file():
        import pytest

        pytest.skip("web/dist not built")

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert b'<div id="root">' in resp.content


def test_spa_mount_skipped_when_dist_missing(tmp_path):
    """create_app must not crash when the SPA build directory is absent."""

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
        spa_dist=tmp_path / "does-not-exist",
    )
    # POST messages still works.
    resp = TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "y"})
    assert resp.status_code == 202


def test_create_app_requires_explicit_spec():
    """`spec` is a required keyword: there's no internal fallback that
    builds a SpecStar from scratch. Callers go through
    `workspace_app.resources.make_spec` (the only public entry) so
    `register_all` stays an implementation detail. The old
    Optional[spec] behaviour was removed when register_all moved into
    make_spec — the explicit TypeError here documents that move."""
    import pytest

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.sandbox.mock import MockSandbox

    with pytest.raises(TypeError, match="spec"):
        create_app(  # ty: ignore[missing-argument]
            sandbox=MockSandbox(),
            filestore=None,  # ty: ignore[invalid-argument-type]  # never reached
            runner=ScriptedAgentRunner([RunDone()]),
        )


async def test_list_files_returns_path_size_pairs(harness: Harness):
    await harness.filestore.write(harness.iid, "/a.txt", b"hello")
    await harness.filestore.write(harness.iid, "/sub/b.txt", b"world!")

    resp = harness.client.get(harness.wpath("/files"))
    assert resp.status_code == 200
    by_path = {it["path"]: it["size"] for it in resp.json()}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 6}


async def test_list_files_prefix_filter(harness: Harness):
    await harness.filestore.write(harness.iid, "/src/a.py", b"a")
    await harness.filestore.write(harness.iid, "/src/b.py", b"b")
    await harness.filestore.write(harness.iid, "/README", b"r")
    resp = harness.client.get(harness.wpath("/files?prefix=/src/"))
    paths = [it["path"] for it in resp.json()]
    assert sorted(paths) == ["/src/a.py", "/src/b.py"]


async def test_list_files_marks_readonly_dir_entries(harness: Harness):
    """Files under the reserved ``.readonly/`` directory (#205) are flagged
    ``read_only`` so the IDE renders them non-editable; everything else is editable."""
    await harness.filestore.write(harness.iid, "/a.txt", b"hi")
    await harness.filestore.write(harness.iid, "/.readonly/context-card.current.md", b"snap")
    resp = harness.client.get(harness.wpath("/files"))
    assert resp.status_code == 200
    ro = {it["path"]: it["read_only"] for it in resp.json()}
    assert ro == {"/a.txt": False, "/.readonly/context-card.current.md": True}


async def test_put_to_readonly_path_is_forbidden(harness: Harness):
    """A write under ``.readonly/`` is server-enforced read-only (#205) — the snapshot
    the human diffs against can't be hand-edited via the IDE. Normal paths still write."""
    ok = harness.client.put(harness.wpath("/files/notes.md"), content=b"hi")
    assert ok.status_code == 204
    blocked = harness.client.put(
        harness.wpath("/files/.readonly/context-card.current.md"), content=b"nope"
    )
    assert blocked.status_code == 403
    assert not await harness.filestore.exists(harness.iid, "/.readonly/context-card.current.md")


async def test_read_file_returns_text_for_utf8(harness: Harness):
    await harness.filestore.write(harness.iid, "/a.txt", b"hello")
    resp = harness.client.get(harness.wpath("/files/a.txt"))
    assert resp.status_code == 200
    assert resp.content == b"hello"
    assert resp.headers["content-type"].startswith("text/plain")


async def test_read_file_returns_octet_stream_for_binary(harness: Harness):
    await harness.filestore.write(harness.iid, "/bin", b"\xff\xfe\x00\x01")
    resp = harness.client.get(harness.wpath("/files/bin"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")


async def test_read_file_returns_image_mime_for_known_extension(harness: Harness):
    """Issue #40: `![foo](./foo.png)` in workspace reports failed to
    render because the file endpoint served PNG bytes with
    `application/octet-stream` — browsers offered a download instead
    of rendering inline. Sniffing extension → MIME fixes the common
    case (png/jpg/svg/gif/webp) without needing to read the bytes."""
    # Tiny valid PNG header so the test data isn't bogus, though the
    # MIME sniff only reads the extension.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    await harness.filestore.write(harness.iid, "/diagrams/flow.png", png_bytes)
    resp = harness.client.get(harness.wpath("/files/diagrams/flow.png"))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == png_bytes


async def test_read_file_falls_back_to_utf8_when_extension_unknown(harness: Harness):
    """When the extension isn't in the MIME table (no extension, weird
    extension), keep the previous "UTF-8 sniff → text/plain else
    octet-stream" behaviour so existing FE renderers keep working."""
    await harness.filestore.write(harness.iid, "/noext", b"hello plain")
    resp = harness.client.get(harness.wpath("/files/noext"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


def test_read_file_missing_returns_404(harness: Harness):
    resp = harness.client.get(harness.wpath("/files/nope"))
    assert resp.status_code == 404


async def test_put_file_writes_raw_bytes(harness: Harness):
    """PUT /a/{slug}/items/{id}/files/{path} stores raw bytes; the FE
    auto-saves notebooks via this endpoint."""
    resp = harness.client.put(harness.wpath("/files/notes.txt"), content=b"hello world")
    assert resp.status_code == 204
    # Round-trip through the public read endpoint.
    got = harness.client.get(harness.wpath("/files/notes.txt"))
    assert got.status_code == 200
    assert got.content == b"hello world"


async def test_put_file_overwrites(harness: Harness):
    harness.client.put(harness.wpath("/files/x"), content=b"first")
    harness.client.put(harness.wpath("/files/x"), content=b"second")
    got = harness.client.get(harness.wpath("/files/x"))
    assert got.content == b"second"


async def test_put_file_into_nested_path(harness: Harness):
    """Path segments are preserved verbatim — FE uses this to save
    notebook files at /report.v3.md, /data/foo.csv, etc."""
    harness.client.put(harness.wpath("/files/report.v3.md"), content=b"# v3")
    got = harness.client.get(harness.wpath("/files/report.v3.md"))
    assert got.content == b"# v3"


def test_runner_exception_is_emitted_as_error_event():

    from workspace_app.api import create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox

    class _Boom:
        async def run(self, prompt, ctx):
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this an async generator

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_Boom(),
    )
    resp = TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "y"})
    assert resp.status_code == 202
    # #43: the turn ran in the worker; its failure persists as an error message
    # (and broadcasts on .../stream) rather than streaming back in the POST body.
    conv = next(
        r.data
        for r in spec.get_resource_manager(Conversation).list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    err = next(m for m in conv.messages if m.role == "error")
    assert "boom" in err.content


def test_message_enhancements_thread_to_the_kb_bridge():
    """The composer's knowledge-search depth applies to the RCA turn's
    `ask_knowledge_base` lookups: body.enhancements lands on the RCA
    ctx AND rides the sub-agent bridge into the KB agent's ctx (where
    kb_search's cascade consumes it). infer_modules keeps operator
    defaults — depth is about answering from the KB, not the focused
    classification probe."""
    from workspace_app.kb.retriever import Enhancements

    spec = make_spec(default_user="u")

    iid = register_rca_item(spec)
    captured: dict[str, object] = {}

    class _Capture:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, prompt, ctx):
            self.calls += 1
            if self.calls == 1:  # the RCA turn — exercise the bridge
                captured["rca"] = ctx.kb_enhancements
                assert ctx.run_subagent is not None
                await ctx.run_subagent("kb_chat", "what is F14?", None, ctx.investigation_id)
                await ctx.run_subagent("infer_modules", "{}", None, ctx.investigation_id)
            elif self.calls == 2:  # the KB sub-agent
                captured["kb"] = ctx.kb_enhancements
            else:  # the infer_modules sub-agent
                captured["infer"] = ctx.kb_enhancements
            yield RunDone()

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Capture()
    )
    client = TestClient(app)
    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "q", "enhancements": {"expand": 3, "rerank": False}},
    )

    want = Enhancements(expand=3, hyde=None, rerank=False)
    assert captured["rca"] == want
    assert captured["kb"] == want
    assert captured["infer"] is None


def test_user_message_records_its_sender():
    """#43: in a shared workspace, each user message records WHO sent it
    (get_user_id) so the multi-user chat can show the author. In prod
    get_user_id reads the request's identity; here we inject one."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([RunDone()]),
        get_user_id=lambda: "alice",
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    user_msg = next(m for m in conv.messages if m.role == "user")
    assert user_msg.author == "alice"
