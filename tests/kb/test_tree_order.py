"""plan-rag-context P2 — the document-tree order the context walk follows.

The cases live in `tree_order.golden.json`, which the frontend's file-tree test
reads too: the order users SEE is the order the algorithm WALKS, and the two
implementations cannot drift without one of the suites going red."""

import json
from pathlib import Path

from workspace_app.kb.tree_order import tree_sort_key

_GOLDEN = Path(__file__).with_name("tree_order.golden.json")


def _cases() -> list[dict]:
    cases = json.loads(_GOLDEN.read_text(encoding="utf-8"))["cases"]
    assert cases, "the golden fixture must not be empty"
    return cases


def test_golden_cases_sort_as_expected():
    for case in _cases():
        got = sorted(case["input"], key=tree_sort_key)
        assert got == case["expected"], case["name"]


def test_golden_expected_is_a_permutation_of_input():
    # A typo in a case's `expected` must not pass by accident.
    for case in _cases():
        assert sorted(case["input"]) == sorted(case["expected"]), case["name"]


def test_order_is_total_and_stable():
    # Sorting a reversed input must give the same order — no dependence on the
    # incoming order, which is what "well-defined" means for users.
    for case in _cases():
        forward = sorted(case["input"], key=tree_sort_key)
        backward = sorted(reversed(case["input"]), key=tree_sort_key)
        assert forward == backward, case["name"]
