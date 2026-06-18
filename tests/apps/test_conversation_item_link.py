from specstar import QB, SpecStar

from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.resources import Conversation


def test_conversation_links_to_any_app_item_by_item_id(spec_instance: SpecStar):
    """Conversation is decoupled from a single `investigation` model — it points
    at the owning item by an opaque, indexed `item_id` (here a RcaInvestigation's
    resource_id) and is queryable by it."""
    inv_rm = spec_instance.get_resource_manager(RcaInvestigation)
    conv_rm = spec_instance.get_resource_manager(Conversation)

    item = inv_rm.create(RcaInvestigation(title="x", owner="alice"))
    conv_rm.create(Conversation(item_id=item.resource_id))

    rows = [r.data for r in conv_rm.list_resources((QB["item_id"] == item.resource_id).build())]
    assert len(rows) == 1
    assert rows[0].item_id == item.resource_id  # ty: ignore[unresolved-attribute]


# ── Phase 6: multi-chat data model (manual §3) ───────────────────────────


def test_conversation_defaults_for_the_new_multi_chat_fields():
    """A bare Conversation is a free chat (no run) with no title + no birth stamp —
    so existing stored conversations decode unchanged (back-compat, manual §3)."""
    conv = Conversation(item_id="it")
    assert conv.title == ""
    assert conv.run_id is None
    assert conv.created_ms is None


def test_item_can_hold_multiple_conversations_each_addressable_with_a_title(
    spec_instance: SpecStar,
):
    """An item holds many conversations (manual §3): a free chat + a workflow chat,
    each with its own stable resource_id, title, and (for the workflow chat) run_id."""
    inv_rm = spec_instance.get_resource_manager(RcaInvestigation)
    conv_rm = spec_instance.get_resource_manager(Conversation)
    item = inv_rm.create(RcaInvestigation(title="x", owner="alice"))

    free = conv_rm.create(Conversation(item_id=item.resource_id, title="Free chat", created_ms=1))
    wf = conv_rm.create(
        Conversation(item_id=item.resource_id, title="memory run", run_id="run-1", created_ms=2)
    )

    rows: dict[str, Conversation] = {}
    for r in conv_rm.list_resources((QB["item_id"] == item.resource_id).build()):
        data = r.data
        assert isinstance(data, Conversation)  # narrow Struct | UnsetType for ty
        rows[r.info.resource_id] = data  # ty: ignore[unresolved-attribute]
    assert len(rows) == 2
    assert free.resource_id != wf.resource_id
    assert sorted(c.title for c in rows.values()) == ["Free chat", "memory run"]
    assert rows[wf.resource_id].run_id == "run-1"
    assert rows[free.resource_id].run_id is None
