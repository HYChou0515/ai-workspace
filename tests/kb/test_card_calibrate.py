"""Calibrating the judge against the only ground truth there is: the owner.

`defines_rate` is the one metric here built on a model's opinion, so it is the
one that can be confidently wrong. What settles it is a person marking a handful
of cards — and that is the ONLY part a person has to do. Rewriting the judge's
criterion from the disagreements is prompt-writing, which is what models are
for, so it happens on the owner's machine with their own model rather than by
sending examples somewhere and waiting for a patch.

The marks are frozen at labelling time. A review file records both the verdict
the judge gave and the owner's answer, so a later judge can be scored against
what the owner actually thought rather than against its own predecessor.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.cards.calibrate import agreement, calibrate, labels
from workspace_app.kb.llm import ILlm


def _review(*rows: tuple[str, bool, bool | None]) -> list[dict]:
    return [{"title": t, "body": f"{t} 的說明", "judge": j, "ok": ok} for t, j, ok in rows]


def test_a_mark_the_owner_did_not_make_means_the_verdict_stood():
    """Marking only the disagreements is the whole point — 20 cards becomes three.
    An unmarked row is not missing data; it is agreement, recorded by omission."""
    assert labels(_review(("A", True, None), ("B", False, True))) == {"A": True, "B": True}


def test_agreement_counts_where_the_judge_matches_the_owner():
    truth = {"A": True, "B": False, "C": True}
    got = agreement({"A": True, "B": True, "C": True}, truth)

    assert got["agreement"] == 2
    assert got["reviewed"] == 3
    assert got["disagreed"] == ["B"]


def test_a_calibration_round_files_the_judge_prompt_it_ran_and_the_one_it_got_back(tmp_path):
    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text(
        json.dumps(_review(("A", True, None), ("B", True, False)), ensure_ascii=False)
    )

    class _Model(ILlm):
        def __init__(self) -> None:
            self.asked: list[str] = []

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "REVISED CRITERION" in prompt:
                self.asked.append(prompt)
                yield "A SHARPER CRITERION\n{cards}", False
            else:
                yield (
                    '{"verdicts": [{"title": "A", "defines": true},'
                    ' {"title": "B", "defines": true}]}',
                    False,
                )

    model = _Model()
    version = calibrate(model, rounds_dir=rounds)

    assert version == 0
    card = json.loads((rounds / "judge" / "v0" / "scorecard.json").read_text())
    assert card["agreement"] == 1 and card["reviewed"] == 2
    assert card["disagreed"] == ["B"]
    assert (rounds / "judge" / "v1" / "prompt.txt").read_text() == "A SHARPER CRITERION\n{cards}"


def test_the_reviser_is_shown_the_cards_it_got_wrong_and_what_the_owner_said(tmp_path):
    """A criterion cannot be repaired from a score. What repairs it is the card
    it misjudged, beside the answer it should have given."""
    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text(
        json.dumps(_review(("14k ratio", True, False)), ensure_ascii=False)
    )

    class _Model(ILlm):
        def __init__(self) -> None:
            self.asked: list[str] = []

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "REVISED CRITERION" in prompt:
                self.asked.append(prompt)
                yield "SHARPER\n{cards}", False
            else:
                yield '{"verdicts": [{"title": "14k ratio", "defines": true}]}', False

    model = _Model()
    calibrate(model, rounds_dir=rounds)

    (asked,) = model.asked
    assert "14k ratio" in asked
    assert "14k ratio 的說明" in asked, "the card body never reached the reviser"
    assert "the judge said it DOES" in asked or "judge said" in asked


def test_a_revision_that_lost_the_cards_placeholder_is_refused(tmp_path):
    """Without `{cards}` the judge would be handed nothing to judge, and would
    answer about nothing without raising."""
    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text(json.dumps(_review(("A", True, False)), ensure_ascii=False))

    class _Model(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "REVISED CRITERION" in prompt:
                yield "Judge them well.", False
            else:
                yield '{"verdicts": [{"title": "A", "defines": true}]}', False

    calibrate(_Model(), rounds_dir=rounds)

    kept = (rounds / "judge" / "v1" / "prompt.txt").read_text()
    assert "{cards}" in kept


def test_the_next_round_scores_the_prompt_the_last_one_wrote(tmp_path):
    """Two triggers, two versions, and the second one graded — otherwise a
    revision is written and never tested."""
    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text(json.dumps(_review(("A", True, None)), ensure_ascii=False))

    class _Model(ILlm):
        def __init__(self) -> None:
            self.judged_with: list[str] = []

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "REVISED CRITERION" in prompt:
                yield "SECOND TRY\n{cards}", False
            else:
                self.judged_with.append(prompt)
                yield '{"verdicts": [{"title": "A", "defines": true}]}', False

    model = _Model()
    assert calibrate(model, rounds_dir=rounds) == 0
    assert calibrate(model, rounds_dir=rounds) == 1
    assert model.judged_with[-1].startswith("SECOND TRY")


def test_the_index_puts_every_judge_version_side_by_side(tmp_path):
    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text(json.dumps(_review(("A", True, None)), ensure_ascii=False))

    class _Model(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "REVISED CRITERION" in prompt:
                yield "NEXT\n{cards}", False
            else:
                yield '{"verdicts": [{"title": "A", "defines": true}]}', False

    calibrate(_Model(), rounds_dir=rounds)
    calibrate(_Model(), rounds_dir=rounds)

    index = json.loads((rounds / "judge" / "index.json").read_text())
    assert [row["version"] for row in index] == [0, 1]
    assert all("agreement" in row for row in index)


def test_nothing_marked_is_refused_rather_than_scored_as_perfect(tmp_path):
    """An empty review agrees with everything. Reporting that as a calibrated
    judge is how a loop ends up steering on a number nobody checked."""
    import pytest

    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text("[]")

    class _Model(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield "{}", False  # pragma: no cover

    with pytest.raises(SystemExit, match="review"):
        calibrate(_Model(), rounds_dir=rounds)


def test_a_version_whose_score_was_deleted_is_skipped_not_fatal(tmp_path):
    """Throwing away a bad round is a reasonable thing to do by hand. Deleting a
    scorecard is how you say "run this version again" — and doing so must not
    take the record of the later versions with it."""
    import json as _json

    rounds = tmp_path / "rounds-cards"
    rounds.mkdir()
    (rounds / "review.json").write_text(_json.dumps(_review(("A", True, None)), ensure_ascii=False))

    class _Model(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "REVISED CRITERION" in prompt:
                yield "NEXT\n{cards}", False
            else:
                yield '{"verdicts": [{"title": "A", "defines": true}]}', False

    calibrate(_Model(), rounds_dir=rounds)
    calibrate(_Model(), rounds_dir=rounds)
    (rounds / "judge" / "v0" / "scorecard.json").unlink()  # thrown away by hand
    calibrate(_Model(), rounds_dir=rounds)

    # Deleting a score means "run this one again" — and re-running v0 must not
    # erase the versions that came after it from the record.
    index = _json.loads((rounds / "judge" / "index.json").read_text())
    assert [row["version"] for row in index] == [0, 1]
