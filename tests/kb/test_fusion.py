from workspace_app.kb.fusion import mmr, reciprocal_rank_fusion


def _sim(near: dict[tuple[str, str], float]):
    def f(x: str, y: str) -> float:
        return near.get((x, y), near.get((y, x), 0.1))

    return f


def test_mmr_prefers_a_distinct_item_over_a_near_duplicate():
    out = mmr(
        ["a", "a2", "b"],
        relevance={"a": 1.0, "a2": 0.95, "b": 0.8},
        similarity=_sim({("a", "a2"): 0.9}),  # a/a2 near-duplicates
        lambda_=0.5,
    )
    # a is most relevant; then b wins over the near-dup a2 despite lower relevance
    assert out == ["a", "b", "a2"]


def test_mmr_respects_k_limit():
    out = mmr(["a", "b", "c"], relevance={"a": 1.0, "b": 0.9, "c": 0.8}, similarity=_sim({}), k=2)
    assert out == ["a", "b"]


def test_rrf_ranks_items_appearing_high_in_multiple_lists_first():
    # "b" is top of list 2 and 2nd of list 1 → should beat "a" (top of list 1 only)
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]])
    assert fused[0] == "b"
    assert set(fused) == {"a", "b", "c", "d"}  # union of all lists


def test_rrf_is_deterministic_on_ties():
    # identical single-item lists → tie broken deterministically by key
    assert reciprocal_rank_fusion([["y"], ["x"]]) == ["x", "y"]
