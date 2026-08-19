"""Scoring a run is deterministic, and says which rule broke.

An LLM judge was deliberately not used: "did it call ask_user" and "does the
answer name the dtype" have objective answers, and a judge would add a second
thing needing calibration before the first could be trusted.
"""

import json

import msgspec
import pytest

from workspace_app.skill_eval.scenario import Expect, Scenario, check, load_scenarios


def scenario(**expect) -> Scenario:
    return Scenario(name="s", prompt="q", expect=Expect(**expect))


def test_a_run_meeting_every_rule_passes():
    v = check(scenario(must_call=["exec"], must_not_call=["ask_user"]), ["exec"], "done")
    assert v.passed
    assert v.failures == []


def test_a_missing_call_names_the_tool_and_what_was_called_instead():
    v = check(scenario(must_call=["show_file"]), ["exec"], "")
    assert [f.rule for f in v.failures] == ["must_call"]
    assert "show_file" in v.failures[0].detail
    assert "exec" in v.failures[0].detail


def test_a_missing_call_says_none_when_nothing_was_called():
    v = check(scenario(must_call=["exec"]), [], "")
    assert "(none)" in v.failures[0].detail


def test_a_forbidden_call_fails():
    v = check(scenario(must_not_call=["ask_user"]), ["exec", "ask_user"], "")
    assert [f.rule for f in v.failures] == ["must_not_call"]


def test_a_phrase_is_matched_case_insensitively():
    assert check(scenario(must_mention=["Object"]), [], "loaded as object").passed


def test_any_one_alternative_satisfies_a_phrase():
    s = scenario(must_mention=[["not numeric", "as text", "object"]])
    assert check(s, [], "the column arrived AS TEXT").passed
    v = check(s, [], "the column is fine")
    assert v.failures[0].rule == "must_mention"
    assert "as text" in v.failures[0].detail


def test_a_forbidden_phrase_reports_which_alternative_hit():
    v = check(scenario(must_not_mention=[["approximately", "roughly"]]), [], "roughly 3")
    assert v.failures[0].detail == "answer names 'roughly'"


def test_every_broken_rule_is_reported_not_just_the_first():
    v = check(
        scenario(must_call=["exec"], must_not_call=["ask_user"], must_mention=["sigma"]),
        ["ask_user"],
        "here you go",
    )
    assert {f.rule for f in v.failures} == {"must_call", "must_not_call", "must_mention"}


def test_scenarios_load_from_a_folder_in_filename_order(tmp_path):
    for name in ("b", "a"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"name": name, "prompt": "q"}))
    assert [s.name for s in load_scenarios(tmp_path)] == ["a", "b"]


def test_an_empty_folder_yields_no_scenarios(tmp_path):
    assert load_scenarios(tmp_path) == []


def test_a_scenario_states_only_what_it_means_to_state(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"name": "s", "prompt": "q"}))
    only = load_scenarios(tmp_path)[0]
    assert only.expect == Expect()
    assert check(only, [], "").passed


def test_a_malformed_scenario_fails_loudly(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"prompt": "q"}))
    with pytest.raises(msgspec.ValidationError):
        load_scenarios(tmp_path)


def test_a_forbidden_phrase_that_never_appears_passes():
    assert check(scenario(must_not_mention=[["roughly", "about"]]), [], "exactly 3.5").passed
