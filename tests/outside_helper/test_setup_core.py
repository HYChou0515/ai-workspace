"""Installing the helper on the person's PC (docs/plan-outside-helper.md D16,
§3.7.1): standard library only, because it runs before anything is installed.

The commands it runs (venv, pip, the self-test, the shortcut) go through an
injected `run`, recorded here; the folders it builds are real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from outside_helper import setup_core
from outside_helper.setup_core import (
    SetupFailed,
    install,
    install_version,
    read_state,
    shortcut_command,
    write_state,
)


class _Run:
    """Stands in for subprocess: records each command; `python -m venv`
    makes the venv's interpreter, as the real one would."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.commands: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, argv: list[str]) -> None:
        self.commands.append(argv)
        if self.fail_on and any(self.fail_on in a for a in argv):
            raise SetupFailed(f"{self.fail_on} failed")
        if argv[1:3] == ["-m", "venv"]:
            python = setup_core.venv_python(Path(argv[3]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")


def _wheelhouse(tmp_path: Path, version: str = "0.3.0") -> Path:
    wh = tmp_path / "zip" / "wheelhouse"
    wh.mkdir(parents=True)
    (wh / f"outside_helper-{version}-py3-none-any.whl").write_text("")
    (wh / "playwright-1.63.0-py3-none-win_amd64.whl").write_text("")
    return wh


def test_a_version_is_installed_into_a_folder_of_its_own(tmp_path):
    home, run = tmp_path / "home", _Run()

    where = install_version(home, "/py/python", _wheelhouse(tmp_path), "0.3.0", run=run)

    assert where == home / "versions" / "0.3.0"
    python = str(setup_core.venv_python(where / "venv"))
    # built by the Python the person confirmed; packages only from the zip;
    # tried before it counts as installed (§3.7.4 steps 3–4)
    assert run.commands == [
        ["/py/python", "-m", "venv", str(home / "versions" / "0.3.0.partial" / "venv")],
        [
            str(setup_core.venv_python(home / "versions" / "0.3.0.partial" / "venv")),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(tmp_path / "zip" / "wheelhouse"),
            "outside-helper==0.3.0",
        ],
        [
            str(setup_core.venv_python(home / "versions" / "0.3.0.partial" / "venv")),
            "-m",
            "outside_helper",
            "--self-test",
        ],
    ]
    assert Path(python).exists()
    assert not (home / "versions" / "0.3.0.partial").exists()


# Half-installed is never installed: a failed step leaves nothing the
# launcher could pick up, and nothing the next try trips over.
@pytest.mark.parametrize("step", ["venv", "pip", "--self-test"])
def test_a_failed_step_leaves_no_version_behind(tmp_path, step):
    home = tmp_path / "home"

    with pytest.raises(SetupFailed):
        install_version(home, "/py/python", _wheelhouse(tmp_path), "0.3.0", run=_Run(step))

    assert list((home / "versions").iterdir()) == []


def test_state_is_written_whole_or_not_at_all(tmp_path):
    write_state(tmp_path, {"current": "0.3.0", "bad": []})

    assert read_state(tmp_path) == {"current": "0.3.0", "bad": []}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]
    (tmp_path / "state.json").write_text("{")
    assert read_state(tmp_path) == {}


# D16: the shortcut names the Python the person confirmed, by its absolute
# path — never the .pyw file association, which may point at another Python.
def test_the_shortcut_runs_the_confirmed_python_on_the_fixed_launcher():
    argv = shortcut_command(
        "Desktop",
        target=r"C:\Python312\pythonw.exe",
        arguments=r'"C:\Users\me\AppData\Local\AIWorkspaceHelper\launch.pyw"',
        workdir=r"C:\Users\me\AppData\Local\AIWorkspaceHelper",
    )

    assert argv[:3] == ["powershell", "-NoProfile", "-Command"]
    script = argv[3]
    assert "GetFolderPath('Desktop')" in script  # follows a redirected Desktop
    assert "AI Workspace Helper.lnk" in script
    assert r"$s.TargetPath = 'C:\Python312\pythonw.exe'" in script
    launcher = r"C:\Users\me\AppData\Local\AIWorkspaceHelper\launch.pyw"
    assert f"""$s.Arguments = '"{launcher}"'""" in script


def test_a_quote_in_a_path_cannot_break_out_of_the_shortcut_script():
    script = shortcut_command("Desktop", target="C:\\it's\\pythonw.exe", arguments="", workdir="")[
        3
    ]

    assert "'C:\\it''s\\pythonw.exe'" in script


def test_install_lays_out_the_folder_and_puts_the_shortcut_on_the_desktop(tmp_path):
    home, run = tmp_path / "home", _Run()
    wheelhouse = _wheelhouse(tmp_path)
    for name in ("launch.pyw", "setup_core.py"):
        (wheelhouse.parent / name).write_text(f"# {name}")

    version = install(wheelhouse.parent, home, base_python="/py/pythonw", run=run)

    assert version == "0.3.0"
    assert read_state(home) == {
        "base_python": "/py/pythonw",
        "current": "0.3.0",
        "previous": None,
        "pending": None,
        "pending_attempts": 0,
        "bad": [],
    }
    assert (home / "launch.pyw").read_text() == "# launch.pyw"
    assert (home / "setup_core.py").read_text() == "# setup_core.py"
    shortcut = run.commands[-1]
    assert shortcut[0] == "powershell" and "GetFolderPath('Desktop')" in shortcut[3]
    assert "'/py/pythonw'" in shortcut[3]


# D16: running install.pyw again (a new Python, a broken install) keeps what
# the person and the helper recorded — the Startup shortcut is still there, so
# the window must still show 「開機時自動啟動」 ticked (review round 1).
def test_installing_again_keeps_what_was_recorded(tmp_path):
    home = tmp_path / "home"
    write_state(home, {"current": "0.2.0", "start_at_login": True, "update": "已更新到 0.2.0"})
    wheelhouse = _wheelhouse(tmp_path)
    for name in ("launch.pyw", "setup_core.py"):
        (wheelhouse.parent / name).write_text(name)

    install(wheelhouse.parent, home, base_python="/py/pythonw", run=_Run())

    state = read_state(home)
    assert (state["current"], state["previous"]) == ("0.3.0", "0.2.0")
    assert state["start_at_login"] is True
    assert state["update"] == "已更新到 0.2.0"


def test_a_zip_with_no_helper_in_it_says_so(tmp_path):
    (tmp_path / "zip" / "wheelhouse").mkdir(parents=True)

    with pytest.raises(SetupFailed, match="outside_helper"):
        install(tmp_path / "zip", tmp_path / "home", base_python="/py/pythonw", run=_Run())


# The fixed launcher starts the version state.json names, windowless.
def test_the_launcher_starts_the_current_version(tmp_path):
    argv = setup_core.launch_command(tmp_path, {"current": "0.3.0"})

    assert argv == [
        str(setup_core.venv_python(tmp_path / "versions" / "0.3.0" / "venv", windowed=True)),
        "-m",
        "outside_helper",
    ]


# On Windows that is pythonw.exe: python.exe would open a console window every
# time the helper starts.
def test_on_windows_the_launcher_starts_it_without_a_console(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_core.sys, "platform", "win32")

    argv = setup_core.launch_command(tmp_path, {"current": "0.3.0"})

    assert argv[0] == str(tmp_path / "versions" / "0.3.0" / "venv" / "Scripts" / "pythonw.exe")
    assert setup_core.venv_python(tmp_path) == tmp_path / "Scripts" / "python.exe"


def test_nothing_installed_is_said_not_launched(tmp_path):
    with pytest.raises(SetupFailed, match="install"):
        setup_core.launch_command(tmp_path, {})


def test_the_state_file_is_json(tmp_path):
    write_state(tmp_path, {"current": "0.3.0"})

    assert json.loads((tmp_path / "state.json").read_text()) == {"current": "0.3.0"}
