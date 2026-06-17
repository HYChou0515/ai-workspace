from workspace_app.kb.context_cards import build_vocab, derive_norm_keys, lookup, match, norm
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard


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
