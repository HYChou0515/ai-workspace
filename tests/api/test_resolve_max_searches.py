"""#334: the composer's per-message kb_search-count pick → the effective cap.

`resolve_max_searches` turns the optional FE value into the budget `max_calls`:
absence inherits the operator default; a concrete pick is clamped to
``[0, ceiling]`` (0 = "don't search this reply").
"""

from __future__ import annotations

from workspace_app.api.kb_chat_routes import resolve_max_searches


def test_absent_inherits_operator_default():
    assert resolve_max_searches(None, default=3, ceiling=10) == 3


def test_absent_inherits_unlimited_default():
    # Operator lifted the cap (null) and the composer sent nothing ⇒ unlimited.
    assert resolve_max_searches(None, default=None, ceiling=10) is None


def test_in_range_pick_passes_through():
    assert resolve_max_searches(5, default=3, ceiling=10) == 5


def test_pick_above_ceiling_clamps_down():
    assert resolve_max_searches(99, default=3, ceiling=10) == 10


def test_zero_is_allowed_no_search():
    assert resolve_max_searches(0, default=3, ceiling=10) == 0


def test_negative_floors_to_zero():
    assert resolve_max_searches(-4, default=3, ceiling=10) == 0
