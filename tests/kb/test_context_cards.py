from workspace_app.kb.context_cards import derive_norm_keys, norm
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


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
