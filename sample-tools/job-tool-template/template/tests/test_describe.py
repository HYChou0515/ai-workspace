"""What the model reads: the author's words about the domain, the template's
words about how a job behaves — the second derived from the contract, so the
two cannot disagree."""

from __future__ import annotations

import pytest
from backends import PATCH, SPEC, Minimal

from job_contract import Contract, Idempotency
from job_tool.describe import describe
from job_tool.run import describe_window


class Described(Minimal):
    contract = Contract(
        spec_schema=SPEC,
        idempotency=Idempotency(fields=("image", "command")),
        domain_description="Trains a model on the shared GPU cluster.",
    )


def test_submit_carries_the_authors_words_and_the_contracts_rules():
    text = describe("submit", Described)

    assert text.startswith("Trains a model on the shared GPU cluster.")
    # Sameness, as the contract declares it — not a sentence typed separately.
    assert "image, command" in text
    # Nothing calls back (D10).
    assert "ask" in text and "later" in text


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (1, "1 second"),
        (60, "1 minute"),
        (3600.0, "1 hour"),
        (86400, "24 hours"),
        (0.5, "0.5 seconds"),
    ],
)
def test_a_window_reads_as_a_person_would_say_it(seconds, said):
    assert describe_window(seconds) == said


def test_sameness_is_said_to_need_the_same_credential_too():
    # A backend finds only the jobs its credential can see: two people in one
    # item with their own tokens are two jobs, and the model must not be told
    # otherwise.
    assert "credential" in describe("submit", Described)


def test_how_long_sameness_lasts_is_said_from_the_contract():
    # D50: the window the backend honours is the one the model is told.
    assert "24 hours" in describe("submit", Described)

    class Brief(Minimal):
        contract = Contract(
            spec_schema=SPEC, idempotency=Idempotency(fields=("image",), window_seconds=90)
        )

    assert "90 seconds" in describe("submit", Brief)


def test_sameness_is_described_per_item_and_the_queue_as_the_whole_queue():
    # D41/D42, said where the model decides: the same work from this item is
    # one job; list shows every job the credential can see, marked by origin.
    assert "same item" in describe("submit", Described)
    listing = describe("list", Minimal)
    assert "from_this_item" in listing and "not only" in listing


def test_get_says_unknown_is_not_gone():
    text = describe("get", Minimal)
    assert "unknown" in text and "not" in text and "exist" in text


def test_update_says_accepted_is_not_applied():
    class Updatable(Minimal):
        contract = Contract(
            spec_schema=SPEC, idempotency=Idempotency(fields=("image",)), patch_schema=PATCH
        )

        def update(self, job_id, patch):
            return self.get(job_id)

    assert "get" in describe("update", Updatable)
