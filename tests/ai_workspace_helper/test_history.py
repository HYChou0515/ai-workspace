"""The helper's 查詢紀錄 (docs/plan-outside-helper.md D13): kept on this PC,
the newest first, at most `keep`, and still there after the helper restarts."""

from __future__ import annotations

from ai_workspace_helper.history import History


def _add(history: History, n: int) -> None:
    history.add(kind="search", args=f"q{n}", site="Google", outcome="done")


def test_the_newest_comes_first_and_survives_a_restart(tmp_path):
    path = tmp_path / "history.jsonl"
    first = History(path, clock=lambda: "2026-10-13T09:00:00")
    _add(first, 1)
    _add(first, 2)

    again = History(path)

    assert [r["args"] for r in again.recent()] == ["q2", "q1"]
    assert again.recent()[0] == {
        "at": "2026-10-13T09:00:00",
        "kind": "search",
        "args": "q2",
        "site": "Google",
        "outcome": "done",
        "reason": "",
    }


def test_it_keeps_only_the_latest(tmp_path):
    path = tmp_path / "history.jsonl"
    history = History(path, keep=3)
    for n in range(10):
        _add(history, n)

    assert [r["args"] for r in history.recent()] == ["q9", "q8", "q7"]
    # the file does not grow without bound either
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 2 * 3
    assert [r["args"] for r in History(path, keep=3).recent()] == ["q9", "q8", "q7"]


def test_a_damaged_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"args": "q1", "kind": "search"}\nnot json\n[1]\n', encoding="utf-8")

    assert [r["args"] for r in History(path).recent()] == ["q1"]


def test_the_window_can_tell_when_there_is_something_new(tmp_path):
    history = History(tmp_path / "history.jsonl")
    before = history.version

    _add(history, 1)

    assert history.version != before


# A file already near the limit (written by an earlier start) is still bounded.
def test_a_long_file_from_an_earlier_start_is_still_bounded(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text("".join(f'{{"args": "old{n}"}}\n' for n in range(5)), encoding="utf-8")
    history = History(path, keep=3)

    _add(history, 1)
    _add(history, 2)

    assert len(path.read_text(encoding="utf-8").splitlines()) <= 2 * 3


# Review round 4: a history is not worth failing a start over.
def test_a_line_cut_inside_a_character_does_not_stop_a_start(tmp_path):
    path = tmp_path / "history.jsonl"
    good = '{"args": "q1", "kind": "search"}\n'
    cut = '{"args": "pandas 新功能", "kind": "search"}'.encode()[:19]  # inside 新
    path.write_bytes(good.encode() + cut)

    assert [r["args"] for r in History(path).recent()] == ["q1"]


# ...and the row after a line cut short is not glued onto it and lost.
def test_the_row_after_a_cut_line_is_kept(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"args": "q1"}\n{"args": "cut', encoding="utf-8")
    history = History(path)

    _add(history, 2)

    assert [r["args"] for r in History(path).recent()] == ["q2", "q1"]


# The rewrite to the newest `keep` replaces the file whole, never leaves it
# half-written: written beside it, then moved over it.
def test_the_rewrite_goes_through_a_file_beside_it(tmp_path, monkeypatch):
    import os

    path = tmp_path / "history.jsonl"
    history = History(path, keep=2)
    replaced: list[tuple[str, str]] = []
    real = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: (replaced.append((str(a), str(b))), real(a, b))[1]
    )
    for n in range(4):
        _add(history, n)

    assert replaced and replaced[-1][1] == str(path)
    assert [r["args"] for r in History(path, keep=2).recent()] == ["q3", "q2"]
