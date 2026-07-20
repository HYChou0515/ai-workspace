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


def test_author_falls_back_to_the_title_as_key_when_no_keys_given(harness):
    cid = _collection(harness.spec)
    # no usable keys (empty list) but a title → the title becomes the key, so the
    # card is still findable by lookup / match.
    harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": [], "title": "Reflow Zone", "body": "b"},
    )
    cards = _cards(harness.spec, cid)
    assert cards[0].keys == ["Reflow Zone"]
    assert cards[0].norm_keys == ["reflow zone"]


def test_author_falls_back_to_title_when_keys_are_only_blank(harness):
    cid = _collection(harness.spec)
    harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["   "], "title": "M4", "body": "b"},
    )
    cards = _cards(harness.spec, cid)
    assert cards[0].keys == ["M4"]
    assert cards[0].norm_keys == ["m4"]


def test_author_action_stores_reference_doc_ids(harness):
    """#518: a card may be authored with the documents that back it."""
    cid = _collection(harness.spec)
    r = harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4"], "body": "b", "reference_doc_ids": ["d1", "d2"]},
    )
    assert r.status_code in (200, 201)
    assert _cards(harness.spec, cid)[0].reference_doc_ids == ["d1", "d2"]


def test_author_action_defaults_reference_doc_ids_to_empty(harness):
    """Omitting the field is the norm (today's FE never sends it) — a card with no
    links behaves exactly as before."""
    cid = _collection(harness.spec)
    harness.client.post("/context-card/author", json={"collection_id": cid, "keys": ["M4"]})
    assert _cards(harness.spec, cid)[0].reference_doc_ids == []


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


def test_edit_action_preserves_reference_doc_ids_when_omitted(harness):
    """#518 REGRESSION GUARD: the edit action rebuilds the whole card struct, so a
    field the FE form doesn't know about is erased on every save. The links must
    survive an edit that never mentions them — otherwise curating a card's evidence
    is undone the next time anyone fixes a typo in its body."""
    cid = _collection(harness.spec)
    harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4"], "body": "b", "reference_doc_ids": ["d1"]},
    )
    rid = _card_ids(harness.spec, cid)[0]

    r = harness.client.post(
        f"/context-card/{rid}/edit", json={"keys": ["M4"], "title": "t2", "body": "b2"}
    )
    assert r.status_code in (200, 201)
    card = _cards(harness.spec, cid)[0]
    assert card.body == "b2"  # the edit landed…
    assert card.reference_doc_ids == ["d1"]  # …without dropping the curated links


def test_edit_action_replaces_reference_doc_ids_when_sent(harness):
    """The other side of the tri-state: sending the field REPLACES the links, and an
    explicit empty list clears them."""
    cid = _collection(harness.spec)
    harness.client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4"], "reference_doc_ids": ["d1"]},
    )
    rid = _card_ids(harness.spec, cid)[0]

    harness.client.post(
        f"/context-card/{rid}/edit", json={"keys": ["M4"], "reference_doc_ids": ["d2", "d3"]}
    )
    assert _cards(harness.spec, cid)[0].reference_doc_ids == ["d2", "d3"]

    harness.client.post(f"/context-card/{rid}/edit", json={"keys": ["M4"], "reference_doc_ids": []})
    assert _cards(harness.spec, cid)[0].reference_doc_ids == []


def test_external_lookup_returns_cards_keyed_by_term(harness):
    cid = _collection(harness.spec)
    harness.client.post(
        "/context-card/author",
        json={
            "collection_id": cid,
            "keys": ["M4", "Capping"],
            "title": "Metal 4",
            "body": "the cap",
        },
    )
    r = harness.client.post(
        f"/kb/collections/{cid}/context-cards/lookup",
        json={"terms": ["m4", "nope"]},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results["m4"][0]["body"] == "the cap"
    assert results["m4"][0]["keys"] == ["M4", "Capping"]
    assert results["m4"][0]["title"] == "Metal 4"
    assert results["nope"] == []  # a miss maps to an empty list, not an error


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


def _norm_keys_of(items: list[dict]) -> list[str]:
    return sorted(k for it in items for k in it["data"]["norm_keys"])


def test_api_user_can_fuzzy_and_substring_query_norm_keys_over_qb(harness):
    # The TrigramIndex on norm_keys lets a direct API user reach a card by a
    # fragment / typo of its key over the same auto CRUD ?qb= route — not just an
    # exact key. (Index-backed on Postgres; correct on every backend.)
    cid = _collection(harness.spec)
    for key in ["capping", "molecular", "reflow zone"]:
        harness.client.post(
            "/context-card/author",
            json={"collection_id": cid, "keys": [key], "title": key, "body": "b"},
        )

    # fuzzy: "capp" is a fragment of "capping" and resolves it (and only it).
    r = harness.client.get(
        "/context-card",
        params={"qb": f"(QB['collection_id'] == '{cid}') & QB['norm_keys'].fuzzy('capp')"},
    )
    assert r.status_code == 200, r.text
    assert _norm_keys_of(r.json()) == ["capping"]

    # substring within an element: "flow" ⊂ "reflow zone" via .any().contains().
    r2 = harness.client.get(
        "/context-card",
        params={"qb": f"(QB['collection_id'] == '{cid}') & QB['norm_keys'].any().contains('flow')"},
    )
    assert r2.status_code == 200, r2.text
    assert _norm_keys_of(r2.json()) == ["reflow zone"]
