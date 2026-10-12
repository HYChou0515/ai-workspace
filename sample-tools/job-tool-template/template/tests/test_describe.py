"""What the model reads: the author's words about the domain, the template's
words about how a job behaves — the second derived from the contract, so the
two cannot disagree."""

from __future__ import annotations

from backends import PATCH, SPEC, Minimal

from job_contract import Contract, Idempotency
from job_tool.describe import describe


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


def test_sameness_is_described_per_item_and_the_queue_as_the_whole_queue():
    # D41/D42, said where the model decides: the same work from this item is
    # one job; list shows every job the credential can see, marked by origin.
    assert "this item" in describe("submit", Described)
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
