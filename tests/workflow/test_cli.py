"""The `python -m workspace_app.workflow` CLI (#287) — `check` (static coherence,
exit 1 on an error) and `new` (scaffold a recipe). Driven through ``main(argv)`` so
the dispatch + exit codes are tested without a subprocess."""

from __future__ import annotations

import json

import pytest

from workspace_app.workflow import cli
from workspace_app.workflow.authoring import Diagnostic


def test_check_all_apps_is_clean_and_exits_zero(capsys):
    assert cli.main(["check"]) == 0
    assert "look good" in capsys.readouterr().out


def test_check_one_slug_exits_zero(capsys):
    assert cli.main(["check", "playground"]) == 0
    assert "look good" in capsys.readouterr().out


def test_check_exits_one_on_an_error(capsys, monkeypatch):
    monkeypatch.setattr(cli, "check_app", lambda s: [Diagnostic("error", f"{s}/p", "boom")])
    assert cli.main(["check", "playground"]) == 1
    out = capsys.readouterr().out
    assert "boom" in out and "1 error(s)" in out


def test_check_with_only_warnings_exits_zero(capsys, monkeypatch):
    monkeypatch.setattr(cli, "check_app", lambda s: [Diagnostic("warning", f"{s}/p", "drift")])
    assert cli.main(["check", "playground"]) == 0
    assert "1 warning(s)" in capsys.readouterr().out


def test_new_scaffolds_into_the_apps_dir(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_apps_dir", lambda: tmp_path)
    assert cli.main(["new", "myapp", "default", "hello"]) == 0
    assert "created 2 file(s)" in capsys.readouterr().out
    entry = json.loads((tmp_path / "myapp/profiles/default/_profile.json").read_text())
    assert entry["workflows"][0]["id"] == "hello"


def test_new_with_a_recipe_and_force(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_apps_dir", lambda: tmp_path)
    assert cli.main(["new", "myapp", "default", "wf", "--recipe", "batch"]) == 0
    # same id again fails without --force, succeeds with it
    assert cli.main(["new", "myapp", "default", "wf"]) == 1
    assert "error:" in capsys.readouterr().out
    assert cli.main(["new", "myapp", "default", "wf", "--recipe", "minimal", "--force"]) == 0


def test_new_rejects_an_unknown_recipe_via_argparse(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_apps_dir", lambda: tmp_path)
    with pytest.raises(SystemExit):  # argparse rejects an invalid --recipe choice
        cli.main(["new", "myapp", "default", "wf", "--recipe", "nope"])


def test_apps_dir_points_at_the_bundled_apps_package():
    # the real (un-monkeypatched) target the scaffold writes into
    assert cli._apps_dir().name == "apps"
