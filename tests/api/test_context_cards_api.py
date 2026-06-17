from specstar import QB

from workspace_app.resources.kb import Collection, ContextCard


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _cards(spec, cid: str) -> list[ContextCard]:
    rm = spec.get_resource_manager(ContextCard)
    return [r.data for r in rm.list_resources((QB["collection_id"] == cid).build())]


def _card_ids(spec, cid: str) -> list[str]:
    rm = spec.get_resource_manager(ContextCard)
    return [r.info.resource_id for r in rm.list_resources((QB["collection_id"] == cid).build())]


def test_author_action_derives_norm_keys(harness):
    cid = _collection(harness.spec)
    r = harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4", "  m4 ", "Capping"], "title": "t", "body": "b"},
    )
    assert r.status_code in (200, 201)
    cards = _cards(harness.spec, cid)
    assert len(cards) == 1
    # client sent raw `keys`; server derived the normalised, deduped, sorted surface.
    assert cards[0].keys == ["M4", "  m4 ", "Capping"]
    assert cards[0].norm_keys == ["capping", "m4"]


def test_edit_action_recomputes_norm_keys(harness):
    cid = _collection(harness.spec)
    harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4"], "title": "t", "body": "b"},
    )
    rid = _card_ids(harness.spec, cid)[0]

    r = harness.client.post(
        f"/context-card/{rid}/edit",
        json={"keys": ["SiCN", "PECVD"], "title": "t2", "body": "b2"},
    )
    assert r.status_code in (200, 201)
    cards = _cards(harness.spec, cid)
    assert len(cards) == 1  # edited in place, not a new card
    assert cards[0].keys == ["SiCN", "PECVD"]
    assert cards[0].norm_keys == ["pecvd", "sicn"]
    assert cards[0].title == "t2"
    assert cards[0].collection_id == cid  # collection preserved across edit


def test_specstar_auto_route_lists_cards_scoped_to_a_collection(harness):
    # No hand-rolled list route — the FE lists a collection's cards through
    # specstar's auto CRUD route, filtered on the indexed `collection_id`.
    cid = _collection(harness.spec, "a")
    other = _collection(harness.spec, "b")
    harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4"], "title": "t", "body": "b"},
    )
    harness.client.post("/context-card/author", json={"collection_id": other, "keys": ["X"]})

    r = harness.client.get(f"/context-card?qb=QB['collection_id'] == '{cid}'")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1  # scoped — the other collection's card is excluded
    assert items[0]["data"]["keys"] == ["M4"]
    assert items[0]["data"]["norm_keys"] == ["m4"]
    assert items[0]["data"]["title"] == "t"
    assert items[0]["revision_info"]["resource_id"].startswith("context-card:")
