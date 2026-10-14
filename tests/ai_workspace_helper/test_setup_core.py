"""Installing the helper on the person's PC (docs/plan-outside-helper.md D16,
§3.7.1): standard library only, because it runs before anything is installed.

The commands it runs (venv, pip, the self-test, the shortcut) go through an
injected `run`, recorded here; the folders it builds are real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_workspace_helper import setup_core
from ai_workspace_helper.setup_core import (
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
            python = setup_core.venv_python(Path(argv[-1]))  # the venv path is last
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")


def _wheelhouse(tmp_path: Path, version: str = "0.3.0") -> Path:
    wh = tmp_path / "zip" / "wheelhouse"
    wh.mkdir(parents=True)
    (wh / f"ai_workspace_helper-{version}-py3-none-any.whl").write_text("")
    (wh / "playwright-1.63.0-py3-none-win_amd64.whl").write_text("")
    return wh


def test_a_version_is_installed_into_a_folder_of_its_own(tmp_path):
    home, run = tmp_path / "home", _Run()

    where = install_version(home, "/py/python", _wheelhouse(tmp_path), "0.3.0", run=run)

    assert where == home / "versions" / "0.3.0"
    python = str(setup_core.venv_python(where / "venv"))
    # built by the Python the person confirmed; packages only from the zip;
    # tried before it counts as installed (§3.7.5 step 1)
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
            "ai-workspace-helper==0.3.0",
        ],
        [
            str(setup_core.venv_python(home / "versions" / "0.3.0.partial" / "venv")),
            "-m",
            "ai_workspace_helper",
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

    installed = install(wheelhouse.parent, home, base_python="/py/pythonw", run=run)

    assert installed == ("0.3.0", "")
    assert read_state(home) == {
        "base_python": "/py/pythonw",
        "current": "0.3.0",
        "previous": None,
    }
    assert (home / "launch.pyw").read_text() == "# launch.pyw"
    assert (home / "setup_core.py").read_text() == "# setup_core.py"
    shortcut = run.commands[-1]
    assert shortcut[0] == "powershell" and "GetFolderPath('Desktop')" in shortcut[3]
    assert "'/py/pythonw'" in shortcut[3]


# D16: running install.pyw again (a new Python, a broken install) keeps what
# the person recorded — the Startup shortcut is still there, so the window must
# still show 「開機時自動啟動」 ticked (review round 1).
def test_installing_again_keeps_what_was_recorded(tmp_path):
    home = tmp_path / "home"
    write_state(home, {"current": "0.2.0", "start_at_login": True})
    wheelhouse = _wheelhouse(tmp_path)
    for name in ("launch.pyw", "setup_core.py"):
        (wheelhouse.parent / name).write_text(name)

    install(wheelhouse.parent, home, base_python="/py/pythonw", run=_Run())

    state = read_state(home)
    assert (state["current"], state["previous"]) == ("0.3.0", "0.2.0")
    assert state["start_at_login"] is True


# D21 (§3.7.5 step 2): every new version is installed this way. The version it
# replaces becomes `previous` — it may still be running, so its folder stays —
# every other version folder goes, and what only the automatic update wrote
# (D15) is dropped.
def test_installing_a_new_version_keeps_the_running_one_and_drops_the_rest(tmp_path):
    home = tmp_path / "home"
    for v in ("0.1.0", "0.2.0"):
        (home / "versions" / v / "venv").mkdir(parents=True)
    write_state(
        home,
        {
            "base_python": "/py/pythonw",
            "current": "0.2.0",
            "previous": "0.1.0",
            "pending": "0.2.1",
            "pending_attempts": 2,
            "bad": ["0.2.1"],
            "failures": {"0.2.2": 1},
            "update": "已更新到 0.2.0",
            "start_at_login": True,
        },
    )
    wheelhouse = _wheelhouse(tmp_path)
    for name in ("launch.pyw", "setup_core.py"):
        (wheelhouse.parent / name).write_text(name)

    install(wheelhouse.parent, home, base_python="/py/pythonw", run=_Run())

    assert read_state(home) == {
        "base_python": "/py/pythonw",
        "current": "0.3.0",
        "previous": "0.2.0",
        "start_at_login": True,
    }
    assert sorted(f.name for f in (home / "versions").iterdir()) == ["0.2.0", "0.3.0"]


# The same version again (a repair): what was previous stays previous.
def test_reinstalling_the_same_version_keeps_the_previous_one(tmp_path):
    home = tmp_path / "home"
    for v in ("0.2.0", "0.3.0"):
        (home / "versions" / v / "venv").mkdir(parents=True)
    write_state(home, {"current": "0.3.0", "previous": "0.2.0"})
    wheelhouse = _wheelhouse(tmp_path)
    for name in ("launch.pyw", "setup_core.py"):
        (wheelhouse.parent / name).write_text(name)

    install(wheelhouse.parent, home, base_python="/py/pythonw", run=_Run())

    assert read_state(home)["previous"] == "0.2.0"
    assert sorted(f.name for f in (home / "versions").iterdir()) == ["0.2.0", "0.3.0"]


def test_a_zip_with_no_helper_in_it_says_so(tmp_path):
    (tmp_path / "zip" / "wheelhouse").mkdir(parents=True)

    with pytest.raises(SetupFailed, match="ai_workspace_helper"):
        install(tmp_path / "zip", tmp_path / "home", base_python="/py/pythonw", run=_Run())


# The fixed launcher starts the version state.json names, windowless.
def test_the_launcher_starts_the_current_version(tmp_path):
    argv = setup_core.launch_command(tmp_path, {"current": "0.3.0"})

    assert argv == [
        str(setup_core.venv_python(tmp_path / "versions" / "0.3.0" / "venv", windowed=True)),
        "-m",
        "ai_workspace_helper",
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


# Without admin rights a PowerShell locked to its constrained language mode
# cannot make the shortcut (New-Object -ComObject). Everything else is in
# place by then: the install stands, and the person is told how to start it.
def test_a_shortcut_that_cannot_be_made_does_not_undo_the_install(tmp_path):
    home = tmp_path / "home"
    wheelhouse = _wheelhouse(tmp_path)
    for name in ("launch.pyw", "setup_core.py"):
        (wheelhouse.parent / name).write_text(name)

    installed = install(wheelhouse.parent, home, base_python="/py/pythonw", run=_Run("WScript"))

    assert installed.version == "0.3.0" and "WScript failed" in installed.shortcut_error
    assert read_state(home)["current"] == "0.3.0"


# `powershell -Command` can carry on past an error and still exit 0: any
# error must fail the command, or a shortcut that was never made reads as made.
def test_the_shortcut_script_stops_at_any_error():
    script = shortcut_command("Desktop", target="t", arguments="", workdir="")[3]

    assert script.startswith("$ErrorActionPreference = 'Stop'; ")


def test_a_shortcut_can_go_to_a_folder_by_its_path():
    script = shortcut_command("", target="t", arguments="", workdir="", in_dir=r"C:\it's")[3]

    assert "$d = 'C:\\it''s'" in script and "GetFolderPath" not in script


# ── the precheck (plan D19): may this PC run the helper, before installing ──


def _zip_with_node(tmp_path: Path) -> Path:
    import zipfile

    wh = tmp_path / "zip" / "wheelhouse"
    wh.mkdir(parents=True)
    with zipfile.ZipFile(wh / "playwright-1.63.0-py3-none-win_amd64.whl", "w") as z:
        z.writestr("playwright/driver/node.exe", "MZ")
    return wh.parent


class _PrecheckRun(_Run):
    """Also makes the shortcut the precheck asks PowerShell for — unless told
    the shortcut fails silently, as PowerShell can."""

    def __init__(self, fail_on: str | None = None, *, silent_shortcut: bool = False) -> None:
        super().__init__(fail_on)
        self.silent_shortcut = silent_shortcut

    def __call__(self, argv: list[str]) -> None:
        super().__call__(argv)
        if argv[0] == "powershell" and not self.silent_shortcut:
            folder = argv[3].split("$d = '")[1].split("'")[0]
            (Path(folder) / "precheck.lnk").write_text("")


def _precheck(tmp_path, run=None, **over):
    from ai_workspace_helper.setup_core import precheck

    chrome = tmp_path / "chrome.exe"
    chrome.write_text("")
    kwargs = {
        "base_python": "/py/pythonw",
        "python_version": (3, 12, 5),
        "chrome_paths": [tmp_path / "nowhere.exe", chrome],
        "chrome_policy": lambda _name: None,
        "run": run or _PrecheckRun(),
        **over,
    }
    return precheck(_zip_with_node(tmp_path), tmp_path / "scratch", **kwargs)


def test_a_pc_that_can_run_the_helper_passes_every_check_and_keeps_nothing(tmp_path):
    run = _PrecheckRun()
    checks = _precheck(tmp_path, run)

    assert [c.ok for c in checks] == [True] * 6, checks
    assert [c.name for c in checks] == [
        "Python 3.10 以上",
        "在使用者資料夾執行程式",
        "Playwright 的 node.exe",
        "建立桌面捷徑（PowerShell）",
        "Chrome",
        "Chrome 允許自動化",
    ]
    ran = [a for a in run.commands]
    assert ran[0][1:4] == ["-m", "venv", "--without-pip"]
    assert ran[2][0].endswith("node.exe") and ran[2][1] == "--version"
    assert not (tmp_path / "scratch").exists()  # it installs nothing


@pytest.mark.parametrize(
    ("breakage", "failed", "hint"),
    [
        ({"python_version": (3, 9, 1)}, "Python 3.10 以上", "3.9.1"),
        ({"run": _PrecheckRun("-c")}, "在使用者資料夾執行程式", "%LOCALAPPDATA%"),
        ({"run": _PrecheckRun("--version")}, "Playwright 的 node.exe", "防毒"),
        ({"run": _PrecheckRun("WScript")}, "建立桌面捷徑（PowerShell）", "PowerShell"),
        ({"run": _PrecheckRun(silent_shortcut=True)}, "建立桌面捷徑（PowerShell）", "PowerShell"),
        ({"chrome_paths": []}, "Chrome", "沒有找到 Chrome"),
        ({"chrome_policy": lambda name: 0}, "Chrome 允許自動化", "RemoteDebuggingAllowed"),
    ],
)
def test_each_check_fails_on_its_own_and_says_what_to_do(tmp_path, breakage, failed, hint):
    checks = _precheck(tmp_path, **breakage)

    bad = [c for c in checks if not c.ok]
    assert [c.name for c in bad] == [failed]
    assert hint in bad[0].detail
    assert len(checks) == 6  # one failing check does not stop the rest
    assert not (tmp_path / "scratch").exists()


def test_a_zip_without_playwright_s_node_says_the_zip_is_incomplete(tmp_path):
    from ai_workspace_helper.setup_core import precheck

    (tmp_path / "zip" / "wheelhouse").mkdir(parents=True)
    checks = precheck(
        tmp_path / "zip",
        tmp_path / "scratch",
        base_python="/py/pythonw",
        python_version=(3, 12, 5),
        chrome_paths=[],
        chrome_policy=lambda _n: None,
        run=_PrecheckRun(),
    )

    node = next(c for c in checks if c.name == "Playwright 的 node.exe")
    assert not node.ok and "zip 不完整" in node.detail


def test_the_summary_says_what_to_do_next():
    from ai_workspace_helper.setup_core import Check, precheck_summary

    ok = [Check("A", True, "可以"), Check("B", True, "可以")]
    assert "接著雙擊 install.pyw" in precheck_summary(ok)
    one_bad = [Check("A", True, "可以"), Check("B", False, "被擋")]
    summary = precheck_summary(one_bad)
    assert "1 項沒有通過" in summary and "✘ B：被擋" in summary and "✔ A：可以" in summary


# The zip carries launch.pyw too (install copies it into the helper's folder).
# Double-clicked there, in the unzipped folder, it said "not installed" though
# the helper was installed: it took its own folder for the helper's (D20).
def test_the_launcher_in_the_unzipped_folder_finds_the_installed_helper(tmp_path, monkeypatch):
    from ai_workspace_helper.setup_core import launcher_home

    home, unzipped = tmp_path / "home", tmp_path / "unzipped"
    unzipped.mkdir()
    write_state(home, {"current": "0.3.0"})
    monkeypatch.setenv("AI_WORKSPACE_HELPER_HOME", str(home))

    assert launcher_home(unzipped) == home
    assert launcher_home(home) == home  # the installed launcher: its own folder


# ...and leaves no trace where the helper would live: after 解除安裝, the
# helper's folder must stay gone (D17), so the lock stays in the unzipped one.
def test_a_launcher_with_nothing_installed_anywhere_still_says_so(tmp_path, monkeypatch):
    from ai_workspace_helper.setup_core import launch, launcher_home

    home, unzipped = tmp_path / "home", tmp_path / "unzipped"
    unzipped.mkdir()
    monkeypatch.setenv("AI_WORKSPACE_HELPER_HOME", str(home))

    with pytest.raises(SetupFailed, match="not installed"):
        launch(launcher_home(unzipped))
    assert not home.exists()


def test_double_clicking_launch_pyw_in_the_unzipped_folder_starts_the_installed_helper(tmp_path):
    """The real launch.pyw, run where the person double-clicked it — the
    unzipped download — starts the version installed in the helper's folder."""
    import os
    import shutil
    import subprocess
    import sys
    import time

    helper = Path(setup_core.__file__).resolve().parents[2]
    home, unzipped = tmp_path / "home", tmp_path / "unzipped"
    unzipped.mkdir()
    shutil.copy(helper / "launch.pyw", unzipped / "launch.pyw")
    shutil.copy(Path(setup_core.__file__), unzipped / "setup_core.py")
    write_state(home, {"current": "0.3.0"})
    started = home / "started"
    python = Path(setup_core.version_command(home, "0.3.0")[0])
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/sh\ntouch '{started}'\n")
    python.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}  # no dialog to show

    done = subprocess.run(
        [sys.executable, str(unzipped / "launch.pyw")],
        env={**env, "AI_WORKSPACE_HELPER_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    for _ in range(50):
        if started.exists():
            break
        time.sleep(0.1)

    assert done.returncode == 0, done.stderr
    assert started.exists()
