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
    assert rows[0].item_id == item.resource_id
