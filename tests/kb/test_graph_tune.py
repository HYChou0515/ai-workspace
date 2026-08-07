"""#697 — the model revising its own extraction prompt, one version per run.

The person who owns the corpus is not always at the keyboard, and writing the
criterion takes many passes. So a round is a single invocation: score, revise,
file. What makes a folder of rounds worth coming back to is that every version
is kept and every version is scored on BOTH sets — a prompt that improved the
passages it was shown and not the ones it was not is the failure mode, and it is
only visible if both were recorded at the time.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.graph.tune import (
    load_prompt,
    next_version,
    revision_prompt,
    run_round,
    scorecard,
)
from workspace_app.kb.llm import ILlm

_REPLY = (
    '{"mentions": [{"surface": "回焊爐", "kind": "機台"},'
    ' {"surface": "245°C", "kind": "parameter"}]}'
)


class _Extractor(ILlm):
    """Answers extraction calls; hands revision calls back as a new prompt."""

    def __init__(self, revision: str = "REVISED\n{text}") -> None:
        self._revision = revision
        self.revision_prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "REVISED PROMPT" in prompt:  # the meta-prompt, not a passage
            self.revision_prompts.append(prompt)
            yield self._revision, False
        else:
            yield _REPLY, False


def _samples(tmp_path):
    for half in ("tune", "holdout"):
        folder = tmp_path / half
        folder.mkdir()
        (folder / "d.txt").write_text("回焊爐 245°C")
    return tmp_path / "tune", tmp_path / "holdout"


def test_a_round_files_the_prompt_it_ran_and_the_one_it_got_back(tmp_path):
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    version = run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    assert version == 0
    assert (rounds / "v0" / "prompt.txt").read_text().startswith("List everything")
    assert (rounds / "v1" / "prompt.txt").read_text() == "REVISED\n{text}"
    assert next_version(rounds) == 1, "the next trigger must continue, not overwrite"


def test_every_version_is_scored_on_both_sets(tmp_path):
    """The holdout is the only thing that can tell tuning from overfitting, and
    it is useless recorded later — it has to be taken at the same time as the
    number it is being compared against."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())
    assert set(card) == {"tune", "holdout"}
    for half in card.values():
        assert half["mentions_per_document"] == 2.0
        assert half["mentions_starting_with_a_digit"] == 1
        assert "回焊爐(機台)" in half["kept"]


def test_the_index_puts_every_version_side_by_side(tmp_path):
    """What a person actually opens on Monday. Overfitting is a SHAPE across
    versions — tune falling while holdout does not — and a shape is not
    something anyone sees by opening twelve folders in turn."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)
    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    index = json.loads((rounds / "index.json").read_text())
    assert [row["version"] for row in index] == [0, 1]
    assert all({"tune", "holdout"} <= set(row) for row in index)


def test_a_revision_that_lost_the_passage_placeholder_is_refused(tmp_path):
    """Without `{text}` every passage extracts to nothing, and the extractor
    never raises — so the next round would score a silent zero and revise from
    THAT. Keeping the previous prompt costs one round instead of the run."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(
        _Extractor(revision="Name only the machines."),
        rounds_dir=rounds,
        tune_dir=tune,
        holdout_dir=holdout,
    )

    assert (rounds / "v1" / "prompt.txt").read_text() == (rounds / "v0" / "prompt.txt").read_text()


def test_a_fenced_revision_is_unwrapped(tmp_path):
    """Models fence things they were told not to fence."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(
        _Extractor(revision="```\nREVISED\n{text}\n```"),
        rounds_dir=rounds,
        tune_dir=tune,
        holdout_dir=holdout,
    )

    assert (rounds / "v1" / "prompt.txt").read_text() == "REVISED\n{text}"


def test_the_model_is_told_what_it_scored_on_the_holdout_and_that_it_is_held_out(tmp_path):
    """A model shown only its tuning score cannot know it is overfitting — and
    neither can whoever reads the round later wondering why it kept going."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    llm = _Extractor()

    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = llm.revision_prompts
    assert "held-out" in asked
    assert "learning the sample" in asked
    # …and the rule that stops it optimising itself into extracting nothing
    assert "WORST" in asked
    # What a name has to BE, stated as a property rather than a list of good
    # words — naming those would mean guessing what the corpus is about from
    # outside it, which is the mistake this module keeps having to unlearn.
    assert "POINT AT IT or LOOK IT UP" in asked
    # …and the real failures, quoted from actual runs rather than imagined
    assert "基礎知識" in asked
    assert "severe" in asked


def test_the_corpus_owner_can_replace_the_examples(tmp_path):
    """Whoever owns the corpus knows its vocabulary; the built-in text only has
    to be right enough to start. Left unreplaceable it would be one more
    criterion written from outside the corpus, which is the failure this whole
    issue exists to fix."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    (rounds / "examples.md").write_text("Only ever name 工單編號. Nothing else counts.")
    llm = _Extractor()

    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = llm.revision_prompts
    assert "工單編號" in asked
    assert "基礎知識" not in asked, "the built-in examples were sent as well as theirs"


def test_the_history_carries_both_columns_so_a_trend_is_visible():
    """The model is asked to improve on what came before, so it has to be able
    to see whether 'before' was improving on the corpus or only on the sample."""
    from workspace_app.kb.graph.tune import Round

    card = {
        "mentions_per_document": 9.0,
        "distinct_names": 4,
        "mentions": 9,
        "mentions_starting_with_a_digit": 2,
        "kinds": {},
        "kept": [],
    }
    asked = revision_prompt(
        "P {text}",
        card,
        card,
        [Round(version=0, prompt="P", tune=card, holdout={**card, "mentions_per_document": 40.0})],
    )
    assert "v0: tune per_doc=9.0" in asked
    assert "holdout per_doc=40.0" in asked


def test_the_first_round_starts_from_the_built_in_prompt(tmp_path):
    assert load_prompt(tmp_path, 0).startswith("List everything")


def test_the_scorecard_shows_the_names_not_only_the_counts(tmp_path):
    """Every count improves when the criterion refuses more, so counts alone
    point at extracting nothing. The names are what make that visible."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = scorecard(rounds / "v0" / "tune")
    assert card["kept"], "a scorecard with no names is a number nobody can check"


def _pool(tmp_path, n: int):
    tune = tmp_path / "tune"
    tune.mkdir()
    for i in range(n):
        (tune / f"{i}.txt").write_text(f"回焊爐 RO-{i}")
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    (holdout / "h.txt").write_text("錫膏")
    return tune, holdout


def _read(rounds, version, half="tune"):
    path = rounds / f"v{version}" / half / "progress.jsonl"
    return {json.loads(x)["document"] for x in path.read_text().splitlines() if x.strip()}


def test_a_round_can_score_a_mini_batch_instead_of_the_whole_tuning_set(tmp_path):
    """Faster per round, so more rounds fit in a weekend — and each round sees a
    DIFFERENT batch, so the prompt cannot learn one fixed set of passages. The
    tuning set stops being a thing to overfit and becomes a pool to draw from."""
    tune, holdout = _pool(tmp_path, 10)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout, batch=3)

    assert len(_read(rounds, 0)) == 3


def test_consecutive_rounds_draw_different_batches(tmp_path):
    """A batch that never changes is the whole tuning set with extra steps."""
    tune, holdout = _pool(tmp_path, 12)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout, batch=3)
    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout, batch=3)

    assert _read(rounds, 0) != _read(rounds, 1), "every round drew the same batch"


def test_the_holdout_can_be_run_less_often_than_the_batch(tmp_path):
    """The holdout is the honest check, so it must stay the SAME documents every
    time or its trend is noise — which makes it the expensive half once the batch
    is small. Running it every few rounds keeps the trend comparable and stops it
    dominating the cost."""
    tune, holdout = _pool(tmp_path, 6)
    rounds = tmp_path / "rounds"

    for _ in range(3):
        run_round(
            _Extractor(),
            rounds_dir=rounds,
            tune_dir=tune,
            holdout_dir=holdout,
            batch=2,
            holdout_every=2,
        )

    ran = [v for v in (0, 1, 2) if (rounds / f"v{v}" / "holdout" / "summary.json").is_file()]
    assert ran == [0, 2], f"the holdout ran on {ran}, not every second round"
    index = json.loads((rounds / "index.json").read_text())
    assert index[1]["holdout"] is None, "a round that skipped the holdout must say so, not guess"
