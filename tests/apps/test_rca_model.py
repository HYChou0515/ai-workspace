import msgspec
from specstar import QB, SpecStar

from workspace_app.apps.base import WorkItemBase
from workspace_app.apps.rca.model import RcaInvestigation, Severity, Status


def test_rca_investigation_round_trips_through_specstar(spec_instance: SpecStar):
    """The RCA App's own resource persists + reloads with its Tier 3 domain
    fields, and applies the RCA defaults (severity P2, status triaging)."""
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    rev = rm.create(RcaInvestigation(title="solder voids", owner="alice"))
    got = rm.get(rev.resource_id).data
    assert got.title == "solder voids"
    assert got.owner == "alice"
    assert got.severity is Severity.P2  # default
    assert got.status is Status.TRIAGING  # default
    assert got.product == ""


def test_rca_investigation_has_tier1_description(spec_instance: SpecStar):
    """`description` is a Tier 1 field on WorkItemBase (decision 12) — present on
    every App's item, defaulting to "" and round-tripping its value."""
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    blank = rm.create(RcaInvestigation(title="x", owner="a")).resource_id
    assert rm.get(blank).data.description == ""
    rid = rm.create(RcaInvestigation(title="y", owner="a", description="oven drift")).resource_id
    assert rm.get(rid).data.description == "oven drift"


def test_rca_investigation_stores_attached_preset(spec_instance: SpecStar):
    """The item records which picker preset drives its turns (#89 decision 23),
    defaulting to "" (AppCatalog then falls back to the profile/first preset)."""
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    blank = rm.create(RcaInvestigation(title="t", owner="a")).resource_id
    assert rm.get(blank).data.attached_preset == ""
    item = RcaInvestigation(title="u", owner="a", attached_preset="claude-opus")
    assert rm.get(rm.create(item).resource_id).data.attached_preset == "claude-opus"


def test_rca_investigation_opts_into_tier2_members_and_topics(spec_instance: SpecStar):
    """RCA opts INTO the Tier 2 features by redeclaring them as concrete lists,
    so they default to ``[]`` (not UNSET) and round-trip their values."""
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    rev = rm.create(RcaInvestigation(title="x", owner="alice"))
    got = rm.get(rev.resource_id).data
    assert got.members == []
    assert got.topics == []

    rev2 = rm.create(
        RcaInvestigation(
            title="y", owner="alice", members=["bob", "carol"], topics=["Reflow zone-3"]
        )
    )
    got2 = rm.get(rev2.resource_id).data
    assert got2.members == ["bob", "carol"]
    assert got2.topics == ["Reflow zone-3"]


def test_workitembase_subclass_without_optin_omits_tier2_on_the_wire(spec_instance: SpecStar):
    """An App that does NOT opt into a Tier 2 feature leaves it ``UNSET``; msgspec
    omits it entirely on the wire (the App simply has no members/topics concept)."""

    class PlainItem(WorkItemBase):  # opts into no Tier 2 feature
        pass

    encoded = msgspec.json.decode(msgspec.json.encode(PlainItem(title="t", owner="o")))
    assert "members" not in encoded
    assert "topics" not in encoded


def test_rca_investigation_filters_by_indexed_severity(spec_instance: SpecStar):
    """`severity` is in `INDEXED_FIELDS`, so filtering by it is a native indexed
    query (not a full scan) — and returns exactly the matching rows."""
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    rm.create(RcaInvestigation(title="a", owner="alice", severity=Severity.P0))
    rm.create(RcaInvestigation(title="b", owner="alice", severity=Severity.P2))
    rm.create(RcaInvestigation(title="c", owner="alice", severity=Severity.P0))

    rows = [r.data for r in rm.list_resources((QB["severity"] == Severity.P0).build())]
    assert {r.title for r in rows} == {"a", "c"}
    assert all(r.severity is Severity.P0 for r in rows)
