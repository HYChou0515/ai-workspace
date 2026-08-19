"""What the operator reads. The control column is the part that earns its place:
a scenario the skill passes AND the control also passes measured nothing."""

from workspace_app.skill_eval.report import Report, render, row_for
from workspace_app.skill_eval.runner import Transcript
from workspace_app.skill_eval.scenario import Expect, Scenario


def scen(name="s", **expect):
    return Scenario(name=name, prompt="q", expect=Expect(**expect), note="why this exists")


def transcript(calls=(), answer="", ended="answered"):
    return Transcript(list(calls), [], answer, len(calls) + 1, ended)


def test_a_row_carries_the_verdict_the_calls_and_the_scenarios_note():
    row = row_for(scen(must_call=["exec"]), transcript(["exec"], "done"))
    assert row.verdict.passed
    assert row.calls == ["exec"]
    assert row.note == "why this exists"


def test_a_row_fails_when_the_run_broke_a_rule():
    assert not row_for(scen(must_call=["exec"]), transcript([], "done")).verdict.passed


def test_the_report_counts_how_many_passed():
    rows = [
        row_for(scen("a", must_call=["exec"]), transcript(["exec"])),
        row_for(scen("b", must_call=["exec"]), transcript([])),
    ]
    assert Report(skill="k", model="m", rows=rows).passed == 1


def test_render_names_the_skill_and_model_and_marks_each_scenario():
    rows = [
        row_for(scen("clean", must_call=["exec"]), transcript(["exec"])),
        row_for(scen("dirty", must_call=["show_file"]), transcript(["exec"])),
    ]
    out = render(Report(skill="verify-number", model="m1", rows=rows))
    assert "verify-number  ×  m1" in out
    assert "clean" in out and "pass" in out
    assert "dirty" in out and "FAIL" in out
    assert "must_call: never called 'show_file'" in out
    assert "1/2 scenarios pass" in out


def test_render_without_a_control_has_no_control_column():
    out = render(Report(skill="k", model="m", rows=[row_for(scen(), transcript())]))
    assert "control" not in out


def test_a_scenario_the_control_also_passes_is_called_out_as_no_evidence():
    row = row_for(scen("easy", must_call=["exec"]), transcript(["exec"]))
    out = render(
        Report(skill="k", model="m", rows=[row]),
        control={"easy": row_for(scen("easy", must_call=["exec"]), transcript(["exec"]))},
    )
    assert "control" in out
    assert "no evidence from easy" in out


def test_a_scenario_only_the_skill_passes_is_not_called_out():
    out = render(
        Report(
            skill="k",
            model="m",
            rows=[row_for(scen("hard", must_call=["exec"]), transcript(["exec"]))],
        ),
        control={"hard": row_for(scen("hard", must_call=["exec"]), transcript([]))},
    )
    assert "no evidence" not in out
    assert "FAIL" in out  # the control's failure is what makes the skill's pass mean something


def test_a_control_missing_a_scenario_renders_a_placeholder_not_a_crash():
    out = render(
        Report(skill="k", model="m", rows=[row_for(scen("x"), transcript())]),
        control={"other": row_for(scen("other"), transcript())},
    )
    assert "-" in out
