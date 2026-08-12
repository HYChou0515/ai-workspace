"""Training the card extraction prompt, one version per run.

The reward turns on one asymmetry: a claim whose quote is not in the document is
dropped before it reaches a card, so everything that survives is grounded BY
CONSTRUCTION. Measuring the surviving cards can never catch a prompt inventing.
Only the ratio of what was OFFERED to what was grounded can — which is why the
extractor counts its own losses, and why that count is what this loop steers on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.cards.tune import run_round
from workspace_app.kb.llm import ILlm


class _Model(ILlm):
    """Offers `invented` extra ungrounded claims alongside a real one."""

    def __init__(self, *, invented: int = 0, revision: str = "REVISED\n{text}") -> None:
        self._invented = invented
        self._revision = revision
        self.revision_prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "REVISED PROMPT" in prompt:
            self.revision_prompts.append(prompt)
            yield self._revision, False
        elif "look up later" in prompt:
            yield '{"names": ["回焊爐", "錫膏"]}', False
        elif "STATEMENTS" in prompt:
            yield json.dumps({"title": "回焊爐", "body": "一種加熱設備"}), False
        else:
            statements = [{"text": "是一種加熱設備", "quote": "回焊爐是一種加熱設備"}]
            statements += [
                {"text": f"編造 {i}", "quote": f"這句話不在文件裡 {i}"}
                for i in range(self._invented)
            ]
            yield (
                json.dumps(
                    {"cards": [{"term": "回焊爐", "keys": ["回焊爐"], "statements": statements}]},
                    ensure_ascii=False,
                ),
                False,
            )


def _samples(tmp_path):
    for half in ("tune", "holdout"):
        folder = tmp_path / half
        folder.mkdir()
        (folder / "d.txt").write_text("回焊爐是一種加熱設備。")
    return tmp_path / "tune", tmp_path / "holdout"


def test_a_round_files_the_prompt_it_ran_and_the_one_it_got_back(tmp_path):
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    version = run_round(_Model(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    assert version == 0
    assert "{text}" in (rounds / "v0" / "prompt.txt").read_text()
    assert (rounds / "v1" / "prompt.txt").read_text() == "REVISED\n{text}"


def test_the_scorecard_measures_inventing_by_what_was_offered(tmp_path):
    """Three claims put forward, one of them really in the text. Scoring the
    cards alone would report a perfectly grounded run."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Model(invented=2), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())["tune"]
    assert card["statements_offered"] == 3
    assert card["statements"] == 1
    assert card["grounded_rate"] == 0.333


def test_the_probe_set_is_the_one_the_graph_loop_uses(tmp_path):
    """Both pipelines are scored against the SAME frozen probes. They extract
    independently — that is the experiment being run — so a shared yardstick is
    the only thing that makes the two runs comparable."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    run_round(_Model(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    assert json.loads((rounds / "probes.json").read_text())["names"] == ["回焊爐", "錫膏"]
    card = json.loads((rounds / "v0" / "scorecard.json").read_text())["holdout"]
    assert card["lookup_hit_rate"] == 0.5, "回焊爐 got a card, 錫膏 did not"
    assert card["probes_missed"] == ["錫膏"]


def test_a_version_that_cards_nothing_cannot_win_on_being_clean(tmp_path):
    """Refusing everything offers nothing, so its grounded rate is a perfect 1.0
    by vacuity. Multiplying by the hit rate is what stops that being the top
    score — the same trap, and the same fix, as on the graph side."""
    from workspace_app.kb.cards.tune import fitness

    empty = {"grounded_rate": 1.0, "lookup_hit_rate": 0.0}
    messy = {"grounded_rate": 0.4, "lookup_hit_rate": 0.8}

    assert fitness(messy) > fitness(empty)


def test_the_reviser_is_told_both_ways_the_prompt_can_fail(tmp_path):
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    model = _Model(invented=1)

    run_round(model, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = model.revision_prompts
    assert "grounded_rate" in asked
    assert "lookup_hit_rate" in asked
    assert "WORST" in asked, "nothing told it that extracting nothing is the worst outcome"


def test_a_probe_matches_the_way_lookup_glossary_matches(tmp_path):
    """`lookup_glossary` folds width and case before comparing, so a probe that
    differs from the card's key only that way IS answerable. Scoring it stricter
    than the tool being modelled would report a miss no reader could ever
    experience — and send the loop chasing it."""
    from workspace_app.kb.cards.tune import probe_score

    out = tmp_path / "out"
    out.mkdir()
    (out / "cards.json").write_text(
        '[{"keys": ["AOI"], "title": "AOI", "body": "", "statements": [], "sources": []}]'
    )

    assert probe_score(out, ["ＡＯＩ"])["lookup_hit_rate"] == 1.0


def test_the_holdout_can_be_run_less_often_than_the_batch(tmp_path):
    """The holdout has to be the SAME documents every time or its trend is noise
    rather than a signal — which makes it the expensive half once the batch is
    small. A round that skips it records nothing rather than carrying the last
    number forward as if it were fresh."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    for _ in range(2):
        run_round(_Model(), rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout, holdout_every=2)

    assert (rounds / "v0" / "holdout").is_dir()
    assert not (rounds / "v1" / "holdout").exists(), "the holdout ran on a round it should skip"
    index = json.loads((rounds / "index.json").read_text())
    assert index[1]["holdout"] is None, "a round that skipped it must say so, not guess"


def test_card_versions_live_apart_from_the_samples_they_share(tmp_path):
    """The two pipelines read the SAME documents and are scored against the SAME
    probes — that is the whole basis for comparing them. Their versions must not
    share a folder even so: both write `vN/prompt.txt` and `index.json`, so one
    loop would silently overwrite the other's history."""
    shared = tmp_path / "rounds"
    shared.mkdir()
    tune, holdout = _samples(shared)
    (shared / "v0").mkdir()
    (shared / "v0" / "prompt.txt").write_text("the graph loop's version, untouched")
    mine = tmp_path / "rounds-cards"

    run_round(
        _Model(),
        rounds_dir=mine,
        tune_dir=tune,
        holdout_dir=holdout,
        probes_dir=shared,
    )

    assert (shared / "v0" / "prompt.txt").read_text() == "the graph loop's version, untouched"
    assert "{text}" in (mine / "v0" / "prompt.txt").read_text()
    assert (shared / "probes.json").is_file(), "the probe set belongs to the shared draw"
    assert not (mine / "probes.json").exists(), "a second probe set is a second yardstick"


def test_the_reviser_is_shown_what_going_wrong_actually_looked_like(tmp_path):
    """Abstract failure modes are not evidence. What made the graph loop work was
    the corpus owner's OWN bad output quoted back — a model told "do not invent"
    has nothing to compare against, and a criterion written from outside the
    corpus is the mistake this whole design exists to unlearn."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    model = _Model()

    run_round(model, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = model.revision_prompts
    assert "薔薇科" in asked, "the invented-definition example never reached the reviser"
    assert "紅色的水果" in asked, "nor did what the right answer looks like"


def test_the_corpus_owner_can_replace_the_examples(tmp_path):
    """Whoever owns the corpus knows what its bad cards look like; the built-in
    text only has to be right enough to start."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    (rounds / "examples.md").write_text("我們的壞卡長這樣:工單編號被寫成一段流程說明。")
    model = _Model()

    run_round(model, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    (asked,) = model.revision_prompts
    assert "工單編號" in asked
    assert "薔薇科" not in asked, "the built-in examples were sent as well as theirs"


# --- a card must say something about the TERM, not about one occasion ---------
#
# 「H2O2 是這次的材料」 is a true, quotable, perfectly grounded sentence and a
# useless card: "這次" points at the document it came from, so outside that
# document it points at nothing. The quote gate cannot catch it — the sentence
# really is in the text. What catches it is reading the card WITHOUT the
# document and asking whether anything survives.


def test_the_judge_sees_the_card_alone_and_not_the_document():
    """Handing it the source would let it resolve 「這次」 from context — which is
    exactly the thing a reader looking the term up will not have."""
    from workspace_app.kb.cards.tune import defines_score

    class _Judge(ILlm):
        def __init__(self) -> None:
            self.asked: list[str] = []

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            self.asked.append(prompt)
            yield '{"verdicts": [{"title": "H2O2", "defines": false}]}', False

    judge = _Judge()
    cards = [{"title": "H2O2", "body": "是這次的材料"}]

    got = defines_score(judge, cards)

    assert got["defines_rate"] == 0.0
    assert got["does_not_define"] == ["H2O2"]
    (asked,) = judge.asked
    assert "是這次的材料" in asked
    assert "H2O2 是這次的材料。本次實驗" not in asked, "the source document was handed over too"


def test_a_judge_that_answers_unusably_reports_nothing_rather_than_zero():
    """A parse failure and "none of them stand alone" are the same number and
    opposite facts."""
    from workspace_app.kb.cards.tune import defines_score

    class _Broken(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield "Hard to say, really.", False

    assert defines_score(_Broken(), [{"title": "H2O2", "body": "x"}]) == {}


def test_no_cards_means_no_judgement_rather_than_a_clean_sheet():
    from workspace_app.kb.cards.tune import defines_score

    class _NeverCalled(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            raise AssertionError("the judge was asked about an empty glossary")
            yield "", False  # pragma: no cover

    assert defines_score(_NeverCalled(), []) == {}


def test_a_glossary_of_useless_but_grounded_cards_does_not_win():
    """Every card quotable, every probe answered, and every card meaningless out
    of context. Without standalone in the fitness the loop would call that a
    perfect run."""
    from workspace_app.kb.cards.tune import fitness

    episodic = {"lookup_hit_rate": 1.0, "grounded_rate": 1.0, "defines_rate": 0.1}
    useful = {"lookup_hit_rate": 0.7, "grounded_rate": 0.8, "defines_rate": 0.9}

    assert fitness(useful) > fitness(episodic)


def test_the_round_judges_the_cards_it_built(tmp_path):
    """The wiring, not the function."""
    tune, holdout = _samples(tmp_path)
    rounds = tmp_path / "rounds"

    class _Judging(_Model):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if "do they know what" in prompt:
                yield '{"verdicts": [{"title": "回焊爐", "defines": true}]}', False
            else:
                yield from super().stream(prompt)

    model = _Judging()
    run_round(model, rounds_dir=rounds, tune_dir=tune, holdout_dir=holdout)

    card = json.loads((rounds / "v0" / "scorecard.json").read_text())["holdout"]
    assert card["defines_rate"] == 1.0
    (asked,) = model.revision_prompts
    assert "defines_rate" in asked


def test_the_extraction_prompt_asks_for_claims_that_outlive_the_document():
    """The prompt is where this is actually fixed; the number only tells the loop
    whether the fix worked."""
    from workspace_app.kb.cards.extract import built_in_prompt

    text = built_in_prompt()
    assert "這次" in text or "this run" in text or "occasion" in text


def test_a_card_that_reports_a_finding_does_not_define_its_term():
    """The second shape, and the one a standalone reading misses: "14k ratio
    increased from 7% in wave 1 to 20% in wave 2" resolves perfectly well on its
    own. Nothing is invented and nothing is ambiguous — it just answers a
    different question from the one a reader opening the card had."""
    from workspace_app.kb.cards.tune import DEFINES, defines_score

    class _Judge(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield '{"verdicts": [{"title": "14k ratio", "defines": false}]}', False

    got = defines_score(
        _Judge(),
        [{"title": "14k ratio", "body": "increased from 7% in wave 1 to 20% in wave 2"}],
    )

    assert got["defines_rate"] == 0.0
    assert got["does_not_define"] == ["14k ratio"]
    assert "FINDING" in DEFINES, "the judge was never told this shape counts"


def test_the_extraction_prompt_turns_findings_away_too():
    """The prompt is where it is actually fixed; the number only says whether the
    fix worked."""
    from workspace_app.kb.cards.extract import built_in_prompt

    assert "14k ratio" in built_in_prompt()


def test_every_shape_of_unusable_verdict_reports_nothing():
    """Braces are not agreement. A reply that parses but carries no verdict is
    the same non-answer as unparseable text, and neither may read as zero — that
    would tell the reviser to loosen a criterion nothing ever assessed."""
    from workspace_app.kb.cards.tune import defines_score

    class _Says(ILlm):
        def __init__(self, reply: str) -> None:
            self.reply = reply

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield self.reply, False

    cards = [{"title": "H2O2", "body": "x"}]
    for reply in (
        "no json at all",  # nothing to slice
        '{"verdicts": [}',  # braces closed, contents unparseable
        '{"verdicts": "not a list"}',  # parses, wrong shape
        '{"verdicts": ["a string"]}',  # a list, but of nothing usable
        "{}",  # an object with no verdicts key
    ):
        assert defines_score(_Says(reply), cards) == {}, reply
