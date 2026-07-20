from workspace_app.kb.eval.sample import into_batches, select_sample

_IDS = [f"c{i}" for i in range(100)]


def test_select_sample_is_deterministic_and_capped_at_n():
    a = select_sample(_IDS, seed="s1", n=10)
    assert a == select_sample(_IDS, seed="s1", n=10)
    assert len(a) == 10
    assert set(a) <= set(_IDS)


def test_select_sample_differs_by_seed():
    assert select_sample(_IDS, "s1", 10) != select_sample(_IDS, "s2", 10)


def test_select_sample_is_stable_regardless_of_input_order():
    assert select_sample(_IDS, "s1", 10) == select_sample(list(reversed(_IDS)), "s1", 10)


def test_select_sample_returns_all_when_fewer_than_n():
    assert sorted(select_sample(["a", "b"], "s", 10)) == ["a", "b"]


def test_into_batches_splits_with_a_trailing_remainder():
    assert into_batches([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
