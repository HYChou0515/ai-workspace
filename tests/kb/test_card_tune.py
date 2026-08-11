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
