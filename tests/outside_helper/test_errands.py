"""What every errand shares (docs/plan-outside-helper.md §3.4): waiting for the
person when a page puts a verification in the way."""

from __future__ import annotations

import pytest
from outside_helper.errands import ErrandContext, ErrandFailed


class _Page:
    def __init__(self) -> None:
        self.fronted = 0

    def bring_to_front(self) -> None:
        self.fronted += 1


def _ctx(page, notes, clock):
    return ErrandContext(
        page=page,
        note=notes.append,
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        clock=lambda: clock[0],
    )


def test_a_verification_brings_the_window_forward_and_waits_for_the_person():
    page, notes, clock = _Page(), [], [0.0]
    answers = iter([True, True, True, False])

    _ctx(page, notes, clock).wait_for_person(lambda: next(answers))

    assert page.fronted == 1
    # the card says so while it waits, and stops saying so once it is solved
    assert notes == ["captcha", ""]


# A person may take minutes over a verification; the job they work on must not
# lapse back to the queue meanwhile (a claim lasts 120 s) — the card is told
# again, which renews the claim, every RENEW_S.
def test_a_long_verification_keeps_the_job_held():
    from outside_helper.errands import RENEW_S

    page, notes, clock = _Page(), [], [0.0]
    answers = iter([True] * 100 + [False])

    _ctx(page, notes, clock).wait_for_person(lambda: next(answers))

    assert notes.count("captcha") == 1 + int(99 // RENEW_S)
    assert notes[-1] == ""
    assert RENEW_S < 120  # inside the backend's CLAIM_TIMEOUT_MS


def test_a_page_with_nothing_in_the_way_does_not_wait():
    page, notes, clock = _Page(), [], [0.0]

    _ctx(page, notes, clock).wait_for_person(lambda: False)

    assert (page.fronted, notes, clock) == (0, [], [0.0])


def test_a_verification_nobody_completes_fails_the_errand():
    page, notes, clock = _Page(), [], [0.0]

    with pytest.raises(ErrandFailed, match="verification"):
        _ctx(page, notes, clock).wait_for_person(lambda: True, timeout_s=30)

    assert clock[0] == pytest.approx(30, abs=1)
