"""#195/#334: the KbSearchBudget value object — cap + counter shared by a turn."""

from __future__ import annotations

from workspace_app.agent import KbSearchBudget


def test_default_is_unlimited_and_counted():
    b = KbSearchBudget()
    assert b.max_calls is None
    assert b.used == 0
    assert b.exhausted is False  # unlimited never exhausts
    assert b.remaining is None  # unlimited has no finite remainder


def test_exhausts_at_the_cap():
    b = KbSearchBudget(max_calls=2)
    assert b.exhausted is False
    assert b.remaining == 2
    b.used = 1
    assert b.exhausted is False
    assert b.remaining == 1
    b.used = 2
    assert b.exhausted is True
    assert b.remaining == 0


def test_zero_cap_is_exhausted_immediately():
    b = KbSearchBudget(max_calls=0)
    assert b.exhausted is True  # #334 Q4: no searches allowed at all
    assert b.remaining == 0


def test_remaining_never_negative_if_overspent():
    b = KbSearchBudget(max_calls=2, used=5)
    assert b.remaining == 0
    assert b.exhausted is True
