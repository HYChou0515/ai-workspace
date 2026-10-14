"""Starting, replacing and removing the helper on the person's PC
(docs/plan-outside-helper.md §3.7.5, D13, D17, D21). Never in place: a new
version is installed beside the running one by install.pyw, which asks the
running helper to end before starting the new one; nothing half-done is ever
started.

All on Linux with the commands recorded (D14: Windows is checked by hand).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_workspace_helper import setup_core
from ai_workspace_helper.setup_core import (
    SetupFailed,
    read_state,
    startup_command,
    uninstall,
    write_state,
)

from tests.ai_workspace_helper.test_setup_core import _Run


def _installed(home: Path, current: str = "0.3.0", **state) -> None:
    for v in {current, *([state["previous"]] if state.get("previous") else [])}:
        (home / "versions" / v / "venv").mkdir(parents=True, exist_ok=True)
    write_state(home, {"base_python": "/py/pythonw", "current": current, "previous": None, **state})


# ── D13: start at login ─────────────────────────────────────────────────────


def test_start_at_login_puts_the_same_shortcut_in_startup(tmp_path):
    on = startup_command(True, base_python=r"C:\Py\pythonw.exe", home=Path(r"C:\H"))
    off = startup_command(False, base_python=r"C:\Py\pythonw.exe", home=Path(r"C:\H"))

    assert "GetFolderPath('Startup')" in on[3] and "CreateShortcut" in on[3]
    assert "GetFolderPath('Startup')" in off[3] and "Remove-Item" in off[3]
    assert "AI Workspace Helper.lnk" in off[3]


# ── D17: uninstall ──────────────────────────────────────────────────────────


def test_uninstall_removes_the_folder_and_both_shortcuts(tmp_path):
    home = tmp_path / "AIWorkspaceHelper"
    _installed(home)
    run = _Run()

    left = uninstall(home, run=run)

    assert left == []
    assert not home.exists()
    folders = sorted(c[3].split("GetFolderPath(")[1].split(")")[0] for c in run.commands)
    assert folders == ["'Desktop'", "'Startup'"]
    assert all("Remove-Item" in c[3] for c in run.commands)


def test_what_uninstall_cannot_remove_is_listed(tmp_path):
    home = tmp_path / "AIWorkspaceHelper"
    _installed(home)

    def stuck(path, onexc=None):  # an antivirus holding a file
        raise PermissionError(13, "in use", str(home / "versions"))

    left = uninstall(home, run=_Run(), remove_tree=stuck)

    assert left == [str(home)]


# ── launch.pyw (§3.7.5) ─────────────────────────────────────────────────────


def test_the_launcher_starts_the_current_version(tmp_path):
    _installed(tmp_path)
    started: list[list[str]] = []

    assert setup_core.launch(tmp_path, popen=lambda argv, cwd: started.append(argv)) == "0.3.0"

    assert started == [setup_core.version_command(tmp_path, "0.3.0")]


# A state.json from D15's automatic update (0.1.x) still starts its current
# version: the fields only that update wrote are not read (D21).
def test_a_state_left_by_the_automatic_update_still_starts_its_current_version(tmp_path):
    _installed(tmp_path, pending="0.4.0", pending_attempts=2, bad=["0.3.1"])
    started: list[list[str]] = []

    assert setup_core.launch(tmp_path, popen=lambda argv, cwd: started.append(argv)) == "0.3.0"

    assert started == [setup_core.version_command(tmp_path, "0.3.0")]
    assert read_state(tmp_path)["pending"] == "0.4.0"  # left as it was, unread


# One helper at a time (§3.7 table): started at login AND double-clicked, the
# second one brings the first one's window forward instead of starting.
def test_with_a_helper_running_the_launcher_brings_it_forward_instead(tmp_path):
    _installed(tmp_path)
    running = setup_core.InstanceLock(tmp_path / "helper.lock")
    assert running.acquire()
    started: list[list[str]] = []
    try:
        outcome = setup_core.launch(tmp_path, popen=lambda argv, cwd: started.append(argv))
    finally:
        running.release()

    assert outcome == "shown"
    assert started == []
    assert (tmp_path / "show.request").exists()


def test_whether_a_helper_is_running_is_asked_of_its_lock(tmp_path):
    _installed(tmp_path)
    assert setup_core.helper_running(tmp_path) is False
    running = setup_core.InstanceLock(tmp_path / "helper.lock")
    assert running.acquire()
    try:
        assert setup_core.helper_running(tmp_path) is True
    finally:
        running.release()
    # asking took nothing: a helper can still start
    assert setup_core.InstanceLock(tmp_path / "helper.lock").acquire()


# D21 (§3.7.5 step 3): install.pyw put a new version in place while a helper
# runs. Asked to, that helper ends (quit.request) and the new one starts once
# its lock is free — never two at a time.
def test_a_new_install_ends_the_running_helper_and_starts_the_new_one(tmp_path):
    _installed(tmp_path, current="0.4.0")
    running = setup_core.InstanceLock(tmp_path / "helper.lock")
    assert running.acquire()
    asked: list[bool] = []

    def sleep(_s: float) -> None:
        # the running helper sees the request and ends, letting go of its lock
        asked.append((tmp_path / setup_core.QUIT_REQUEST).exists())
        running.release()

    started: list[list[str]] = []
    outcome = setup_core.replace_running(
        tmp_path, popen=lambda argv, cwd: started.append(argv), wait_s=20, sleep=sleep
    )

    assert asked == [True]
    assert outcome == "0.4.0"
    assert started == [setup_core.version_command(tmp_path, "0.4.0")]
    assert not (tmp_path / setup_core.QUIT_REQUEST).exists()


# A helper that does not end in time is left running and brought forward; its
# request is withdrawn so it does not end later, under the person's feet.
def test_a_helper_that_does_not_end_in_time_is_left_running(tmp_path):
    _installed(tmp_path, current="0.4.0")
    running = setup_core.InstanceLock(tmp_path / "helper.lock")
    assert running.acquire()
    started: list[list[str]] = []
    try:
        outcome = setup_core.replace_running(
            tmp_path, popen=lambda argv, cwd: started.append(argv), wait_s=2, sleep=lambda _s: None
        )
    finally:
        running.release()

    assert outcome == "shown" and started == []
    assert not (tmp_path / setup_core.QUIT_REQUEST).exists()
    assert (tmp_path / setup_core.SHOW_REQUEST).exists()


# A request left from before (its helper ended on its own first) must not end
# the next helper the moment it starts: holding the lock, the launcher knows no
# helper is running, so any request there is stale.
def test_a_stale_quit_request_does_not_end_the_next_helper(tmp_path):
    _installed(tmp_path)
    (tmp_path / setup_core.QUIT_REQUEST).write_text("quit")

    setup_core.launch(tmp_path, popen=lambda argv, cwd: None)

    assert not (tmp_path / setup_core.QUIT_REQUEST).exists()


# The way from 0.1.x (D15) to D21: a 0.1.x that reaches AI Workspace still
# downloads the next version itself and marks it pending, and ITS launcher
# starts it — counting starts, and after three without the confirmation D15
# expected, rolling it back for good. So the new version, once started, takes
# over at once: current, the old one previous, D15's fields gone, and the
# launcher and setup_core.py it brought put in the helper's folder.
def test_a_version_started_as_d15s_pending_one_takes_over(tmp_path):
    _installed(tmp_path, current="0.1.0", pending="0.2.0", pending_attempts=1, bad=[])
    (tmp_path / "versions" / "0.2.0" / "venv").mkdir(parents=True)
    for name in ("launch.pyw", "setup_core.py"):
        (tmp_path / name).write_text(f"old {name}")
        (tmp_path / "versions" / "0.2.0" / name).write_text(f"new {name}")
    (tmp_path / "versions" / "0.0.9").mkdir()

    assert setup_core.take_over(tmp_path, "0.2.0") is True

    assert read_state(tmp_path) == {
        "base_python": "/py/pythonw",
        "current": "0.2.0",
        "previous": "0.1.0",
    }
    assert (tmp_path / "launch.pyw").read_text() == "new launch.pyw"
    assert (tmp_path / "setup_core.py").read_text() == "new setup_core.py"
    assert sorted(f.name for f in (tmp_path / "versions").iterdir()) == ["0.1.0", "0.2.0"]


# Started any other way (installed by install.pyw: nothing pending, or another
# version pending), it changes nothing.
@pytest.mark.parametrize("pending", [None, "0.3.0"])
def test_a_version_not_pending_takes_nothing_over(tmp_path, pending):
    _installed(tmp_path, current="0.2.0", pending=pending)
    before = read_state(tmp_path)

    assert setup_core.take_over(tmp_path, "0.2.0") is False

    assert read_state(tmp_path) == before


def test_with_nothing_installed_the_launcher_says_so(tmp_path):
    with pytest.raises(SetupFailed, match="install"):
        setup_core.launch(tmp_path, popen=lambda argv, cwd: None)


# ── the uninstall clean-up process (D17) ────────────────────────────────────


def test_the_clean_up_waits_for_the_helper_to_end_then_removes_it(tmp_path):
    home = tmp_path / "AIWorkspaceHelper"
    _installed(home)
    running = setup_core.InstanceLock(home / "helper.lock")
    assert running.acquire()
    waited: list[float] = []

    def sleep(s: float) -> None:
        waited.append(s)
        if len(waited) == 3:
            running.release()  # the helper has ended

    left = setup_core.uninstall_main(home, run=_Run(), sleep=sleep, log_dir=tmp_path)

    assert len(waited) == 3
    assert left == [] and not home.exists()
    assert not (tmp_path / "ai-workspace-helper-uninstall.log").exists()


def test_what_the_clean_up_cannot_remove_is_written_down(tmp_path):
    home = tmp_path / "AIWorkspaceHelper"
    _installed(home)

    def stuck(path):
        raise PermissionError(13, "in use")

    left = setup_core.uninstall_main(
        home, run=_Run(), sleep=lambda s: None, log_dir=tmp_path, remove_tree=stuck
    )

    assert left == [str(home)]
    assert str(home) in (tmp_path / "ai-workspace-helper-uninstall.log").read_text(encoding="utf-8")


def _fail_fetch(_file: str) -> bytes:
    raise AssertionError("nothing should be downloaded")
