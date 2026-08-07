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

    def __init__(self, revision: str = "REVISED\n{text}", reply: str = _REPLY) -> None:
        self._revision = revision
        self._reply = reply
        self.revision_prompts: list[str] = []
        self.probe_calls = 0

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "REVISED PROMPT" in prompt:  # the meta-prompt, not a passage
            self.revision_prompts.append(prompt)
            yield self._revision, False
        elif "look up later" in prompt:  # the probe-drawing call
            self.probe_calls += 1
            yield '{"names": ["回焊爐", "錫膏"]}', False
        else:
            yield self._reply, False


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


# --- P1: the downstream reward — can the graph be LOOKED UP? -----------------
#
# Every other number in the scorecard falls when the criterion refuses more, so
# a loop optimising them alone walks towards extracting nothing. This is the one
# signal that moves the other way, and the only one measuring what the graph is
# actually FOR: `lookup_entity(name)` resolves a name through
# `GraphEntity.norm_keys`, so "is it in the graph" means "would that lookup hit".


def test_the_probe_set_is_drawn_once_and_then_frozen(tmp_path):
    """A target redrawn every round is not a target. The probes have to outlive
    the versions they grade, or an improvement and a re-draw are the same shape
    in the numbers."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    llm = _Extractor()

    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)
    first = (rounds / "probes.json").read_text()
    drawn = llm.probe_calls
    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    assert drawn > 0, "no probes were ever drawn"
    assert llm.probe_calls == drawn, "the probes were redrawn — the target moved"
    assert (rounds / "probes.json").read_text() == first


def test_the_holdout_scorecard_says_which_probes_the_graph_cannot_answer(tmp_path):
    """A rate on its own cannot be checked; the names can. Whoever reads the
    round can open the document and see whether a miss was the extractor's fault
    or a probe that was never reasonable."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())
    assert card["holdout"]["probes_total"] == 2
    assert card["holdout"]["lookup_hit_rate"] == 0.5, "回焊爐 was extracted, 錫膏 was not"
    assert card["holdout"]["probes_missed"] == ["錫膏"]


def test_extracting_nothing_scores_zero_here(tmp_path):
    """The whole reason this layer exists. Refusing everything is perfect on
    every other number in the scorecard and is the worst possible outcome; this
    is the number that says so."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(
        _Extractor(reply='{"mentions": []}'),
        rounds_dir=rounds,
        tune_dir=tune,
        holdout_dir=holdout,
    )

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())
    assert card["holdout"]["lookup_hit_rate"] == 0.0


def test_probes_the_owner_wrote_are_used_as_they_are(tmp_path):
    """The probes are a plain list in a plain file precisely so a person can
    strike out the unreasonable ones. Regenerating over their edit would make
    that pointless — and silently."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    (rounds / "probes.json").write_text(json.dumps({"names": ["回焊爐"]}, ensure_ascii=False))
    llm = _Extractor()

    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    assert llm.probe_calls == 0, "the owner's probe set was overwritten"
    card = json.loads((rounds / "v0" / "scorecard.json").read_text())
    assert card["holdout"]["lookup_hit_rate"] == 1.0


def test_a_probe_matches_through_the_same_normalisation_lookup_uses(tmp_path):
    """`lookup_entity` resolves through `norm_surface`, so a probe differing only
    by width or spacing IS answerable and must not count as a miss — scoring it
    stricter than the tool being modelled would make the loop chase a difference
    no user can observe."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    (rounds / "probes.json").write_text(json.dumps({"names": [" 回焊爐 "]}, ensure_ascii=False))

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())
    assert card["holdout"]["lookup_hit_rate"] == 1.0


def test_the_model_is_shown_what_the_graph_could_not_answer(tmp_path):
    """The reviser cannot pull back from over-tightening it cannot see."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    llm = _Extractor()

    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = llm.revision_prompts
    assert "lookup_hit_rate" in asked
    assert "錫膏" in asked, "the reviser was told the rate but not what was missed"


# --- P2: termhood needs TWO dimensions ---------------------------------------
#
# Document frequency alone cannot tell a rare-but-real thing from one-off noise.
# A machine used only in the reflow section appears in one document in eight and
# many times inside it; a passing noun appears in one document, once. On df they
# are identical. The pair (df, times-inside-a-document) separates them, which is
# the C-value/termhood intuition and is 25 years old.


def _pool_text(tmp_path, docs: dict[str, str]):
    tune = tmp_path / "tune"
    tune.mkdir()
    for name, body in docs.items():
        (tune / f"{name}.txt").write_text(body)
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    (holdout / "h.txt").write_text("錫膏")
    return tune, holdout


def test_document_frequency_counts_the_whole_pool(tmp_path):
    """Counting is plain string matching over the raw text — no model call — so
    there is never a reason to estimate it from a subset."""
    from workspace_app.kb.graph.tune import document_frequency

    pool = tmp_path / "pool"
    pool.mkdir()
    for i in range(8):
        (pool / f"{i}.txt").write_text("回焊爐 出現在這裡" if i == 0 else "別的內容")

    assert document_frequency(pool, ["回焊爐"]) == {"回焊爐": (1, 8)}


def test_the_batch_does_not_shrink_the_frequency_denominator(tmp_path):
    """The regression this phase exists to prevent. Eight documents cannot
    estimate a frequency of one-in-eight — the reading is 0 or 1 and looks like
    stable evidence either way, every round. The batch decides how many MODEL
    CALLS a round costs; it must not decide how many samples a statistic is
    estimated from."""
    tune, holdout = _pool_text(tmp_path, {"a": "回焊爐", "b": "x", "c": "y", "d": "z"})
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout, batch=1)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())["tune"]
    assert card["pool_documents"] == 4, "the denominator followed the batch"


def test_a_rare_but_concentrated_name_is_discriminative_not_noise(tmp_path):
    """The trap this phase exists for. `回焊爐` is in one document of four and
    said many times inside it — the most informative shape there is. Punishing
    it for being rare would delete exactly what the graph is for."""
    from workspace_app.kb.graph.tune import quadrants

    counts = {
        # name: (documents containing it, times inside the documents that do)
        "回焊爐": ((1, 4), 9),
        "訊息": ((4, 4), 1),
        "隨手一提": ((1, 4), 1),
        "錫膏": ((3, 4), 6),
    }
    got = quadrants(counts)

    assert got["discriminative"] == ["回焊爐"]
    assert got["furniture"] == ["訊息"]
    assert got["singleton"] == ["隨手一提"]
    assert got["core"] == ["錫膏"]


def test_the_scorecard_carries_the_quadrants_with_their_names(tmp_path):
    """Proportions alone are gameable by extracting nothing — every one of them
    looks perfect against an empty graph. The names are what make the shares
    checkable."""
    tune, holdout = _pool_text(
        tmp_path, {"a": "回焊爐 回焊爐 回焊爐 245°C", "b": "回焊爐 溫度", "c": "別的"}
    )
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())["tune"]
    buckets = ("discriminative", "core", "furniture", "singleton")
    assert all(isinstance(card[b], list) for b in buckets), (
        "a share with no names cannot be checked"
    )
    assert all(f"{b}_share" in card for b in buckets)
    assert "回焊爐" in [n for b in buckets for n in card[b]], "an extracted name landed nowhere"


def test_the_reviser_is_shown_which_names_are_document_furniture(tmp_path):
    """This is what replaces the blacklist. A blacklist teaches the model to
    avoid four particular words; a share plus its members describes the SHAPE,
    which is the thing that generalises to a corpus nobody has looked at."""
    tune, holdout = _pool_text(tmp_path, {"a": "回焊爐 245°C", "b": "回焊爐 245°C"})
    rounds = tmp_path / "rounds"
    llm = _Extractor()

    run_round(llm, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = llm.revision_prompts
    assert "furniture" in asked
    assert "discriminative" in asked
