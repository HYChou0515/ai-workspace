from specstar.types import extract_trigram_index_field_infos

from workspace_app.kb.context_cards import (
    build_vocab,
    card_context_block,
    derive_norm_keys,
    lookup,
    match,
    norm,
)
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard


def test_norm_keys_carries_a_trigram_index():
    """The derived lookup surface opts into a pg_trgm GIN so a direct API user can
    run index-backed substring / fuzzy ``?qb=`` queries over ``norm_keys`` (exact
    membership already rides the shared jsonb ``@>`` GIN; this adds the fuzzy path).
    """
    infos = extract_trigram_index_field_infos(ContextCard)
    assert {i.name: i.is_list for i in infos} == {"norm_keys": True}


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _card(spec, cid: str, keys: list[str], **kw) -> str:
    rm = spec.get_resource_manager(ContextCard)
    card = ContextCard(collection_id=cid, keys=keys, norm_keys=derive_norm_keys(keys), **kw)
    return rm.create(card).resource_id


def test_norm_casefolds_and_collapses_whitespace():
    assert norm("  M4 ") == "m4"


def test_norm_folds_fullwidth_via_nfkc():
    # Full-width "Ｍ４" → NFKC → "M4" → casefold → "m4".
    assert norm("Ｍ４") == "m4"


def test_norm_collapses_internal_whitespace_runs():
    assert norm("Metal   4\tcap") == "metal 4 cap"


def test_derive_norm_keys_is_sorted_unique_and_drops_blanks():
    # "M4" and "  m4 " collapse to one key; the empty string is dropped;
    # result is sorted for a stable, deterministic lookup surface.
    assert derive_norm_keys(["M4", "  m4 ", "Capping", ""]) == ["capping", "m4"]


def test_context_card_round_trip():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    rm = spec.get_resource_manager(ContextCard)
    rev = rm.create(
        ContextCard(
            collection_id=cid,
            keys=["M4", "capping"],
            norm_keys=derive_norm_keys(["M4", "capping"]),
            title="Metal-4 capping",
            body="The capping layer over metal 4.",
        )
    )
    got = rm.get(rev.resource_id).data
    assert got.keys == ["M4", "capping"]
    assert got.norm_keys == ["capping", "m4"]
    assert got.title == "Metal-4 capping"
    assert got.body == "The capping layer over metal 4."
    # #518: a card written without links carries an EMPTY reference list — the
    # additive default is what makes "empty ⇒ today's behaviour" byte-for-byte true.
    assert got.reference_doc_ids == []


def test_context_card_carries_reference_doc_ids():
    """#518: a card may link the documents that back it — the card-anchored precision
    path scopes a vector search to exactly these. Optional and never required."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    rm = spec.get_resource_manager(ContextCard)
    rev = rm.create(
        ContextCard(
            collection_id=cid,
            keys=["M4"],
            norm_keys=derive_norm_keys(["M4"]),
            body="The fourth metal layer.",
            reference_doc_ids=["doc-a", "doc-b"],
        )
    )
    assert rm.get(rev.resource_id).data.reference_doc_ids == ["doc-a", "doc-b"]


def test_lookup_exact_hit_is_normalized():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["M4", "Capping"], body="metal 4 cap")
    res = lookup(spec, cid, ["m4"])  # different case than the stored key
    assert list(res.keys()) == ["m4"]  # keyed by the ORIGINAL input term
    assert [c.body for c in res["m4"]] == ["metal 4 cap"]


def test_lookup_is_membership_not_substring():
    spec = make_spec(default_user="u")
    a = _collection(spec, "a")
    _card(spec, a, ["M40"], body="forty")
    assert lookup(spec, a, ["M4"])["M4"] == []  # "m4" is not an element of {"m40"}

    # Reverse direction in an isolated collection (so only the "M4" card exists):
    b = _collection(spec, "b")
    _card(spec, b, ["M4"], body="four")
    assert lookup(spec, b, ["M40"])["M40"] == []  # "m40" is not an element of {"m4"}


def test_lookup_one_key_hits_multiple_cards():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["etch"], body="a")
    _card(spec, cid, ["etch", "dry-etch"], body="b")
    bodies = sorted(c.body for c in lookup(spec, cid, ["ETCH"])["ETCH"])
    assert bodies == ["a", "b"]


def test_lookup_batch_keys_by_original_term_and_misses_are_empty():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["M4"], body="four")
    res = lookup(spec, cid, ["m4", "nope"])
    assert [c.body for c in res["m4"]] == ["four"]
    assert res["nope"] == []


def test_lookup_is_scoped_to_the_collection():
    spec = make_spec(default_user="u")
    a, b = _collection(spec, "a"), _collection(spec, "b")
    _card(spec, a, ["M4"], body="in-a")
    assert lookup(spec, b, ["m4"])["m4"] == []  # other collection's card excluded


def test_lookup_term_is_normalized_fullwidth():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["M4"], body="four")
    assert [c.body for c in lookup(spec, cid, ["Ｍ４"])["Ｍ４"]] == ["four"]


def _mkcard(keys: list[str], body: str = "") -> ContextCard:
    return ContextCard(collection_id="c", keys=keys, norm_keys=derive_norm_keys(keys), body=body)


def test_match_finds_card_whose_key_appears_in_text():
    vocab = build_vocab([_mkcard(["M4"], "metal4")])
    assert [c.body for c in match("what is M4 anyway", vocab)] == ["metal4"]


def test_match_rejects_ascii_key_glued_into_a_longer_word():
    vocab = build_vocab([_mkcard(["M4"], "metal4"), _mkcard(["etch"], "etching")])
    assert match("the m40 wafer", vocab) == []  # m4 not inside m40
    assert match("ran foobar_etch step", vocab) == []  # etch not inside foobar_etch


def test_match_allows_a_cjk_key_embedded_in_a_sentence():
    vocab = build_vocab([_mkcard(["封蓋製程"], "capping")])
    assert [c.body for c in match("這個封蓋製程的問題", vocab)] == ["capping"]


def test_match_handles_multiword_keys():
    vocab = build_vocab([_mkcard(["Metal 4"], "m4")])
    assert [c.body for c in match("the metal 4 cap failed", vocab)] == ["m4"]


def test_match_dedupes_a_card_hit_by_several_keys():
    vocab = build_vocab([_mkcard(["M4", "capping"], "one")])
    assert [c.body for c in match("M4 capping both present", vocab)] == ["one"]


def test_match_caps_with_deterministic_order():
    cards = [_mkcard(["a"], "A"), _mkcard(["b"], "B"), _mkcard(["c"], "C")]
    vocab = build_vocab(cards)
    # sorted key order a<b<c → first two cards, deterministically.
    assert [c.body for c in match("a b c", vocab, cap=2)] == ["A", "B"]


def test_match_returns_empty_when_no_key_present():
    vocab = build_vocab([_mkcard(["M4"], "metal4")])
    assert match("nothing relevant here", vocab) == []


def test_card_context_block_lists_cards_and_is_empty_when_none():
    assert card_context_block([]) == ""
    block = card_context_block([_mkcard(["M4", "Capping"], "the cap layer over metal 4")])
    assert "the cap layer over metal 4" in block  # the explanation is present
    assert "M4" in block  # and the term it answers


def test_card_context_block_prefers_title_and_shows_keys_as_aliases():
    c = ContextCard(collection_id="c", keys=["M4"], norm_keys=["m4"], title="Metal 4", body="b")
    block = card_context_block([c])
    assert "### Metal 4" in block  # the title is the heading
    assert "(M4)" in block  # the key shown as an alias since it differs from the title


def test_card_context_block_omits_alias_suffix_when_it_equals_the_label():
    block = card_context_block([_mkcard(["M4"], "metal four")])  # no title, one key
    assert "### M4\nmetal four" in block  # label = keys[0]; no redundant "(M4)"


def test_card_context_block_handles_a_card_with_no_keys():
    c = ContextCard(collection_id="c", keys=[], norm_keys=[], title="", body="orphan")
    assert "orphan" in card_context_block([c])  # label falls back to "" but body still shows


# ── #111: find cards by exact key, WITH their resource ids ────────────────


def test_find_cards_by_key_returns_id_card_pairs_for_exact_matches():
    from workspace_app.kb.context_cards import find_cards_by_key

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    rid = _card(spec, cid, ["M4", "Metal 4"], body="four")
    hits = find_cards_by_key(spec, cid, "m4")  # normalised, exact membership
    assert [(i, c.body) for i, c in hits] == [(rid, "four")]


def test_find_cards_by_key_is_membership_not_substring_and_scoped():
    from workspace_app.kb.context_cards import find_cards_by_key

    spec = make_spec(default_user="u")
    a, b = _collection(spec, "a"), _collection(spec, "b")
    _card(spec, a, ["M40"], body="forty")
    assert find_cards_by_key(spec, a, "M4") == []  # "m4" is not the element "m40"
    _card(spec, a, ["M4"], body="in-a")
    assert find_cards_by_key(spec, b, "m4") == []  # other collection excluded
