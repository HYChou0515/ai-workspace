"""The tool double, and the parts of the app's contract it promises to model.

Where it drifts from `agent.tools`, an eval would score the harness instead of
the guidance — so the framing of `exec` output is pinned here, not assumed.
"""

from workspace_app.skill_eval.tools import EXEC_CAP, Event, run, schemas, truncate_middle


def test_write_then_read_round_trips(tmp_path):
    assert "wrote a.txt" in run("write_file", {"path": "a.txt", "content": "hi"}, tmp_path, [])
    assert run("read_file", {"path": "a.txt"}, tmp_path, []) == "hi"


def test_writing_into_a_new_subdirectory_creates_it(tmp_path):
    run("write_file", {"path": "d/e/a.txt", "content": "x"}, tmp_path, [])
    assert (tmp_path / "d" / "e" / "a.txt").read_text() == "x"


def test_write_defaults_to_empty_content(tmp_path):
    run("write_file", {"path": "a.txt"}, tmp_path, [])
    assert (tmp_path / "a.txt").read_text() == ""


def test_reading_a_missing_file_tells_the_model_rather_than_raising(tmp_path):
    assert run("read_file", {"path": "nope"}, tmp_path, []) == "no such file: nope"


def test_listing_marks_directories_with_a_trailing_slash(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("")
    assert run("list_files", {}, tmp_path, []).split("\n") == ["a.txt", "sub/"]


def test_listing_a_missing_directory_tells_the_model(tmp_path):
    assert run("list_files", {"path": "nope"}, tmp_path, []) == "no such directory: nope"


def test_exec_frames_success_with_the_exit_code_and_drops_stderr(tmp_path):
    script = "import sys; print('out'); print('noise', file=sys.stderr)"
    out = run("exec", {"cmd": ["python", "-c", script]}, tmp_path, [])
    assert out.startswith("Tool `exec` returned (exit_code=0):")
    assert "out" in out
    assert "noise" not in out


def test_exec_keeps_stderr_when_the_command_failed(tmp_path):
    out = run("exec", {"cmd": ["python", "-c", "raise SystemExit(3)"]}, tmp_path, [])
    assert "exit_code=3" in out
    assert "--- stderr ---" in out


def test_exec_runs_in_the_workspace_so_relative_paths_work(tmp_path):
    (tmp_path / "d.txt").write_text("data")
    out = run("exec", {"cmd": ["python", "-c", "print(open('d.txt').read())"]}, tmp_path, [])
    assert "data" in out


def test_a_missing_command_is_exit_127_like_the_real_sandbox(tmp_path):
    assert "exit_code=127" in run("exec", {"cmd": ["definitely-not-a-command"]}, tmp_path, [])


def test_show_file_records_an_event_the_scenario_can_score(tmp_path):
    (tmp_path / "c.png").write_bytes(b"x")
    events: list[Event] = []
    assert "shown to the user" in run("show_file", {"path": "c.png"}, tmp_path, events)
    assert events == [Event("show_file", "c.png")]


def test_showing_a_file_that_was_never_written_still_records_the_attempt(tmp_path):
    events: list[Event] = []
    assert run("show_file", {"path": "gone.png"}, tmp_path, events) == "no such file: gone.png"
    assert events == [Event("show_file", "gone.png")]


def test_ask_user_records_the_question_and_ends_the_turn(tmp_path):
    events: list[Event] = []
    assert "put to the user" in run("ask_user", {"question": "which sigma?"}, tmp_path, events)
    assert events == [Event("ask_user", "which sigma?")]


def test_an_unknown_tool_is_reported_rather_than_crashing_the_run(tmp_path):
    assert run("nope", {}, tmp_path, []) == "unknown tool 'nope'"


def test_short_output_is_returned_whole():
    assert truncate_middle("abc", 10) == "abc"


def test_long_output_keeps_the_head_and_the_tail_and_says_what_it_dropped():
    out = truncate_middle("A" * 60 + "B" * 60, 30)
    assert out.startswith("A" * 20)
    assert out.endswith("B" * 10)
    assert "90 chars omitted" in out


def test_every_tool_the_runner_can_dispatch_has_a_schema():
    names = {s["function"]["name"] for s in schemas()}
    assert names == {"write_file", "read_file", "list_files", "exec", "show_file", "ask_user"}
    assert all(s["function"]["description"] for s in schemas())


def test_the_exec_cap_is_the_per_tool_ceiling_not_the_sdk_backstop():
    from workspace_app.config.schema import ExecSettings

    assert ExecSettings().output_max_chars == EXEC_CAP
    assert ExecSettings().tool_output_max_chars != EXEC_CAP


def test_a_command_that_overruns_is_exit_124_like_the_real_sandbox(tmp_path, monkeypatch):
    """The sandbox kills a long-running command and reports 124; the model has to
    see that rather than the harness dying."""
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="sleep", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    out = run("exec", {"cmd": ["sleep", "999"]}, tmp_path, [])
    assert "exit_code=124" in out
    assert "timed out" in out
