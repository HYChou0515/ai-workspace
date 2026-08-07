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


# --- P3: what this version STOPPED finding -----------------------------------
#
# The recall leg that needs nobody to write a list. Measured on the holdout,
# because it is the same documents every time — so a name that disappears is
# attributable to the prompt rather than to the draw.


def test_the_names_this_version_lost_are_measured_on_the_holdout(tmp_path):
    """On the tuning batch a name vanishes whenever the draw changes, which is
    not evidence about the prompt. The holdout is fixed, so a disappearance
    there has exactly one cause."""
    from workspace_app.kb.graph.tune import lost_names

    rounds = tmp_path / "rounds"
    for version, reply in ((0, ["回焊爐", "錫膏"]), (1, ["回焊爐"])):
        out = rounds / f"v{version}" / "holdout"
        out.mkdir(parents=True)
        (out / "mentions.json").write_text(
            json.dumps(
                [
                    {"surface": n, "norm_surface": n, "occurrences": 7, "declared_quote": f"…{n}…"}
                    for n in reply
                ],
                ensure_ascii=False,
            )
        )

    assert [row["surface"] for row in lost_names(rounds, version=1)] == ["錫膏"]


def test_a_lost_name_is_reported_with_the_evidence_needed_to_judge_it(tmp_path):
    """Whether a loss was right is the two-dimensional question again: dropping
    something a document said seven times is suspicious, dropping something said
    once is the point. A bare list of names cannot be judged either way."""
    from workspace_app.kb.graph.tune import lost_names

    rounds = tmp_path / "rounds"
    out = rounds / "v0" / "holdout"
    out.mkdir(parents=True)
    (out / "mentions.json").write_text(
        json.dumps(
            [
                {
                    "surface": "錫膏",
                    "norm_surface": "錫膏",
                    "occurrences": 7,
                    "declared_quote": "…錫膏…",
                }
            ],
            ensure_ascii=False,
        )
    )
    (rounds / "v1" / "holdout").mkdir(parents=True)
    (rounds / "v1" / "holdout" / "mentions.json").write_text("[]")

    (row,) = lost_names(rounds, version=1)
    assert row == {"surface": "錫膏", "occurrences": 7, "quote": "…錫膏…"}


def test_the_first_version_has_lost_nothing(tmp_path):
    """There is no earlier version to have lost it from, and reporting the whole
    vocabulary as 'lost' would tell the model to undo a prompt it never ran."""
    from workspace_app.kb.graph.tune import lost_names

    rounds = tmp_path / "rounds"
    (rounds / "v0" / "holdout").mkdir(parents=True)
    (rounds / "v0" / "holdout" / "mentions.json").write_text("[]")

    assert lost_names(rounds, version=0) == []


def test_a_round_that_skipped_the_holdout_does_not_blind_the_comparison(tmp_path):
    """With `--holdout-every` most rounds have no holdout to compare against.
    Looking only at `version - 1` would find no reference and report nothing
    lost — and "nothing lost" is indistinguishable from a version that really
    lost nothing, so the leg would go quiet on every round but one."""
    from workspace_app.kb.graph.tune import lost_names

    rounds = tmp_path / "rounds"
    for version, names in ((0, ["回焊爐", "錫膏"]), (2, ["回焊爐"])):
        out = rounds / f"v{version}" / "holdout"
        out.mkdir(parents=True)
        (out / "mentions.json").write_text(
            json.dumps(
                [
                    {"surface": n, "norm_surface": n, "occurrences": 3, "declared_quote": ""}
                    for n in names
                ],
                ensure_ascii=False,
            )
        )
    (rounds / "v1").mkdir()  # ran, but skipped the holdout

    assert [row["surface"] for row in lost_names(rounds, version=2)] == ["錫膏"]


def test_the_reviser_is_asked_to_justify_what_it_dropped(tmp_path):
    """The model cannot pull back from a loss it is never told about, and this
    is the leg that costs the corpus owner nothing to maintain — the reference
    set is produced by the loop itself."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)
    second = _Extractor(reply='{"mentions": [{"surface": "回焊爐", "kind": "機台"}]}')
    run_round(second, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = second.revision_prompts
    assert "245°C" in asked, "a name that disappeared was never put to the model"
    assert "stopped finding" in asked


# --- P4: the reviser must see the prompts it already tried -------------------


def test_the_reviser_is_shown_the_text_of_earlier_prompts(tmp_path):
    """Sending scores without the prompts that produced them leaves the model
    hill-climbing blind: it undoes a change, scores worse, redoes it, and
    oscillates. Two numbers per version cannot tell it what it already tried."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    for revision in ("FIRST TRY\n{text}", "SECOND TRY\n{text}"):
        run_round(
            _Extractor(revision=revision), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout
        )
    third = _Extractor(revision="THIRD TRY\n{text}")
    run_round(third, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = third.revision_prompts
    # "SECOND TRY" is what this round ran and is shown anyway; "FIRST TRY" is
    # the one it can only know about from the history.
    assert "FIRST TRY" in asked, "the model cannot see what it already tried and moved away from"


def test_only_the_most_recent_prompts_are_carried(tmp_path):
    """Every prompt ever written would crowd out the evidence — and the oldest
    are the least informative, having already been revised away from."""
    from workspace_app.kb.graph.tune import Round, revision_prompt

    card = {
        "mentions_per_document": 9.0,
        "distinct_names": 4,
        "mentions": 9,
        "mentions_starting_with_a_digit": 2,
        "kinds": {},
        "kept": [],
    }
    history = [Round(version=v, prompt=f"PROMPT_{v}", tune=card, holdout=card) for v in range(6)]

    asked = revision_prompt("P {text}", card, card, history)

    assert "PROMPT_5" in asked
    assert "PROMPT_0" not in asked, "the whole history was pasted in"


# --- P5: revise from the best version, not merely the latest -----------------
#
# Single-line hill climbing has no way back: a bad revision becomes the base for
# the next one, and the good version it came from is on disk but out of play.
# ProTeGi keeps a beam for exactly this reason.


def test_a_round_revises_from_the_best_scoring_version_not_the_last_one(tmp_path):
    """The revision is filed as the next version either way — the beam decides
    which PARENT it is written from, so a regression costs one round instead of
    becoming the base everything after it is measured against."""
    from workspace_app.kb.graph.tune import Round, pick_parent

    def card(hit_rate: float) -> dict:
        return {"lookup_hit_rate": hit_rate, "furniture_share": 0.1, "mentions_per_document": 8.0}

    history = [
        Round(version=0, prompt="GOOD", tune=card(0.9), holdout=card(0.9)),
        Round(version=1, prompt="WORSE", tune=card(0.2), holdout=card(0.2)),
    ]

    assert pick_parent(history).prompt == "GOOD"


def test_the_beam_only_looks_at_versions_the_holdout_actually_scored(tmp_path):
    """A version graded only on its own mini-batch cannot be compared with one
    graded on the fixed set — picking between them would reward a lucky draw."""
    from workspace_app.kb.graph.tune import Round, pick_parent

    def card(hit_rate: float) -> dict:
        return {"lookup_hit_rate": hit_rate, "furniture_share": 0.1, "mentions_per_document": 8.0}

    history = [
        Round(version=0, prompt="GRADED", tune=card(0.5), holdout=card(0.5)),
        Round(version=1, prompt="UNGRADED", tune=card(0.99), holdout=None),
    ]

    assert pick_parent(history).prompt == "GRADED"


def test_a_version_that_found_nothing_never_wins_the_beam(tmp_path):
    """The failure the whole reward design exists to prevent, restated at the
    selection step. An empty graph has a noise share of ZERO, so any penalty that
    is SUBTRACTED hands it the win over a messy version that actually answers
    something — the noise has to be a discount on what was found, not a debt
    against it. These numbers are the discriminating case: subtracting makes the
    empty version score higher."""
    from workspace_app.kb.graph.tune import Round, pick_parent

    empty = {"lookup_hit_rate": 0.0, "furniture_share": 0.0, "singleton_share": 0.0}
    messy = {"lookup_hit_rate": 0.6, "furniture_share": 0.5, "singleton_share": 0.2}
    history = [
        Round(version=0, prompt="MESSY BUT USEFUL", tune=messy, holdout=messy),
        Round(version=1, prompt="EMPTY", tune=empty, holdout=empty),
    ]

    assert pick_parent(history).prompt == "MESSY BUT USEFUL"


class _PerPassage(ILlm):
    """Names whichever known thing the passage actually contains.

    A fake that answers the same way for every passage makes every name equally
    frequent, which flattens exactly the statistics under test. This one varies
    with the text, so document frequency and concentration mean something.
    """

    KNOWN = ("回焊爐", "錫膏", "AOI")

    def __init__(self, *, silent: bool = False) -> None:
        self._silent = silent
        self.revision_prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "REVISED PROMPT" in prompt:
            self.revision_prompts.append(prompt)
            yield "REVISED\n{text}", False
        elif "look up later" in prompt:
            yield '{"names": ["回焊爐", "錫膏"]}', False
        elif self._silent:
            yield '{"mentions": []}', False
        else:
            found = [n for n in self.KNOWN if n in prompt.rsplit("\n", 1)[-1]]
            yield json.dumps({"mentions": [{"surface": n, "kind": "機台"} for n in found]}), False


def _varied(tmp_path):
    """Three documents, each about a different thing said more than once — so a
    name is rare across the pool and concentrated inside one document, the shape
    the quadrants are meant to reward."""
    for half in ("tune", "holdout"):
        folder = tmp_path / half
        folder.mkdir()
        for name in _PerPassage.KNOWN:
            (folder / f"{name}.txt").write_text(f"{name} {name} 說明")
    return tmp_path / "tune", tmp_path / "holdout"


def test_a_bad_round_does_not_become_the_base_for_every_round_after_it(tmp_path):
    """The beam, end to end. v1 finds nothing, so v2 must be written from v0 —
    under single-line climbing v1 would be the base and one bad revision would
    cost every round that followed."""
    tune, holdout = _varied(tmp_path)
    rounds = tmp_path / "rounds"
    # One whitespace token per passage, so a name said twice in a document
    # registers as concentrated rather than as a single mention.
    for extractor in (_PerPassage(), _PerPassage(silent=True)):
        run_round(
            extractor,
            rounds_dir=rounds,
            tune_dir=tune,
            holdout_dir=holdout,
            chunk_tokens=1,
            chunk_overlap=0,
        )

    assert (rounds / "v2" / "parent.txt").read_text() == "0", "the empty version became the base"


def test_the_round_records_which_version_it_revised_from(tmp_path):
    """Without it the folder is unreadable: v7 built from v3 looks like v7 built
    from v6, and nobody can reconstruct why a version appeared."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)
    run_round(_Extractor(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    index = json.loads((rounds / "index.json").read_text())
    assert "revised_from" in index[1]
