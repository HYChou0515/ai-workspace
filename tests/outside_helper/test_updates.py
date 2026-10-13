"""Updating the helper on the person's PC (docs/plan-outside-helper.md §3.7.4–
§3.7.6) and removing it (D17). Never in place: a new version is installed
beside the running one and taken on the next start; one that will not start is
rolled back by the fixed launcher; nothing half-done is ever started.

All on Linux with the commands recorded (D14: Windows is checked by hand).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from outside_helper import setup_core
from outside_helper.setup_core import (
    SetupFailed,
    confirm,
    prepare_update,
    read_state,
    start_choice,
    startup_command,
    uninstall,
    write_state,
)

from tests.outside_helper.test_setup_core import _Run


def _zip(version: str, launcher: str = "# launch v") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"wheelhouse/outside_helper-{version}-py3-none-any.whl", "wheel")
        z.writestr("launch.pyw", f"{launcher}{version}")
        z.writestr("setup_core.py", f"# setup_core {version}")
        z.writestr("install.pyw", "# install")
    return buf.getvalue()


def _manifest(version: str, data: bytes, **over) -> dict:
    return {
        "version": version,
        "min_version": "0.0.0",
        "file": f"ai-workspace-helper-{version}.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
        **over,
    }


def _installed(home: Path, current: str = "0.3.0", **state) -> None:
    for v in {current, *([state["previous"]] if state.get("previous") else [])}:
        (home / "versions" / v / "venv").mkdir(parents=True, exist_ok=True)
    write_state(
        home,
        {
            "base_python": "/py/pythonw",
            "current": current,
            "previous": None,
            "pending": None,
            "pending_attempts": 0,
            "bad": [],
            **state,
        },
    )


# ── §3.7.4: preparing the next version in the background ────────────────────


def test_a_newer_release_is_installed_beside_and_waits_for_the_next_start(tmp_path):
    _installed(tmp_path)
    data = _zip("0.4.0")
    run = _Run()

    outcome = prepare_update(tmp_path, _manifest("0.4.0", data), lambda _f: data, run=run)

    assert outcome == "ready"
    assert (tmp_path / "versions" / "0.4.0" / "venv").is_dir()
    # the running version is untouched, and nothing switches until next start
    state = read_state(tmp_path)
    assert (state["current"], state["pending"], state["pending_attempts"]) == ("0.3.0", "0.4.0", 0)
    # the new version keeps its own launcher, to put in place once it is confirmed
    assert (tmp_path / "versions" / "0.4.0" / "launch.pyw").read_text() == "# launch v0.4.0"
    # the venv is built from the Python the person confirmed at install (D16)
    assert run.commands[0][0] == "/py/pythonw"


@pytest.mark.parametrize("version", ["0.3.0", "0.2.9"])
def test_a_release_no_newer_than_this_one_is_left_alone(tmp_path, version):
    _installed(tmp_path)
    data = _zip(version)

    assert prepare_update(tmp_path, _manifest(version, data), _fail_fetch, run=_Run()) == "current"


def test_a_release_marked_bad_is_not_tried_again(tmp_path):
    _installed(tmp_path, bad=["0.4.0"])
    data = _zip("0.4.0")

    assert prepare_update(tmp_path, _manifest("0.4.0", data), _fail_fetch, run=_Run()) == "bad"


def test_a_release_already_waiting_is_not_fetched_again(tmp_path):
    _installed(tmp_path, pending="0.4.0")
    (tmp_path / "versions" / "0.4.0" / "venv").mkdir(parents=True)
    data = _zip("0.4.0")

    assert prepare_update(tmp_path, _manifest("0.4.0", data), _fail_fetch, run=_Run()) == "ready"


# A download that does not match the manifest is never installed.
def test_a_download_that_does_not_match_is_thrown_away(tmp_path):
    _installed(tmp_path)
    data = _zip("0.4.0")

    with pytest.raises(SetupFailed, match="sha256"):
        prepare_update(tmp_path, _manifest("0.4.0", data), lambda _f: data + b"x", run=_Run())

    assert not (tmp_path / "versions" / "0.4.0").exists()
    assert list((tmp_path / "downloads").iterdir()) == []
    assert read_state(tmp_path)["pending"] is None


# §3.7.4 step 5: the running version carries on; the same release failing three
# times is marked bad and left until the next one.
def test_a_release_that_fails_three_times_is_marked_bad(tmp_path):
    _installed(tmp_path)
    data = _zip("0.4.0")

    for attempt in range(1, 4):
        with pytest.raises(SetupFailed):
            prepare_update(
                tmp_path, _manifest("0.4.0", data), lambda _f: data, run=_Run("--self-test")
            )
        state = read_state(tmp_path)
        assert state["current"] == "0.3.0"
        assert ("0.4.0" in state["bad"]) is (attempt == 3)

    assert prepare_update(tmp_path, _manifest("0.4.0", data), _fail_fetch, run=_Run()) == "bad"


# A zip whose entries climb out of its folder (zip slip) writes nothing — even
# one that matches its manifest's checksum.
def test_an_update_zip_that_names_paths_outside_itself_is_refused(tmp_path):
    home = tmp_path / "home"
    _installed(home)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("wheelhouse/outside_helper-0.4.0-py3-none-any.whl", "wheel")
        z.writestr("../../escaped.txt", "out")
    data = buf.getvalue()

    with pytest.raises(SetupFailed, match="outside"):
        prepare_update(home, _manifest("0.4.0", data), lambda _f: data, run=_Run())

    assert not (tmp_path / "escaped.txt").exists()
    assert not (home / "escaped.txt").exists()


def test_a_manifest_naming_a_path_is_refused(tmp_path):
    _installed(tmp_path)
    data = _zip("0.4.0")

    with pytest.raises(SetupFailed):
        prepare_update(
            tmp_path, _manifest("0.4.0", data, file="../x.zip"), lambda _f: data, run=_Run()
        )


# ── §3.7.5: the fixed launcher switches, and rolls back ──────────────────────


def test_with_nothing_waiting_the_current_version_starts(tmp_path):
    version, state = start_choice({"current": "0.3.0", "pending": None})

    assert version == "0.3.0"
    assert state["pending"] is None


def test_a_waiting_version_is_tried_and_counted(tmp_path):
    version, state = start_choice({"current": "0.3.0", "pending": "0.4.0", "pending_attempts": 1})

    assert version == "0.4.0"
    assert state["pending_attempts"] == 2


def test_a_version_that_never_confirms_is_rolled_back_after_three_starts(tmp_path):
    state = {"current": "0.3.0", "pending": "0.4.0", "pending_attempts": 0, "bad": []}

    started = []
    for _ in range(4):
        version, state = start_choice(state)
        started.append(version)

    assert started == ["0.4.0", "0.4.0", "0.4.0", "0.3.0"]
    assert (state["pending"], state["bad"]) == (None, ["0.4.0"])
    # said once, on the next check-in (§3.7.5 step 4)
    assert state["update"] == "0.4.0 起不來,已退回 0.3.0"


def test_the_new_version_confirms_itself_and_takes_over(tmp_path):
    _installed(tmp_path, current="0.3.0", previous="0.2.0", pending="0.4.0", pending_attempts=1)
    new = tmp_path / "versions" / "0.4.0"
    (new / "venv").mkdir(parents=True)
    (new / "launch.pyw").write_text("# launch v0.4.0")
    (new / "setup_core.py").write_text("# setup_core 0.4.0")
    (tmp_path / "launch.pyw").write_text("# launch v0.3.0")
    (tmp_path / "versions" / "0.1.0" / "venv").mkdir(parents=True)

    assert confirm(tmp_path, "0.4.0") is True

    state = read_state(tmp_path)
    assert (state["current"], state["previous"], state["pending"], state["pending_attempts"]) == (
        "0.4.0",
        "0.3.0",
        None,
        0,
    )
    # only the version running and the one before it are kept
    assert sorted(p.name for p in (tmp_path / "versions").iterdir()) == ["0.3.0", "0.4.0"]
    # the launcher is replaced only now, once the new version has proven itself
    assert (tmp_path / "launch.pyw").read_text() == "# launch v0.4.0"
    assert (tmp_path / "setup_core.py").read_text() == "# setup_core 0.4.0"


def test_a_version_that_is_not_the_one_waiting_confirms_nothing(tmp_path):
    _installed(tmp_path, current="0.3.0", pending="0.4.0")
    (tmp_path / "launch.pyw").write_text("# launch v0.3.0")

    assert confirm(tmp_path, "0.3.0") is False

    assert read_state(tmp_path)["pending"] == "0.4.0"
    assert (tmp_path / "launch.pyw").read_text() == "# launch v0.3.0"


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


def test_the_launcher_starts_the_chosen_version_after_writing_down_the_try(tmp_path):
    _installed(tmp_path, pending="0.4.0", pending_attempts=0)
    started: list[list[str]] = []

    assert setup_core.launch(tmp_path, popen=lambda argv, cwd: started.append(argv)) == "0.4.0"

    assert started == [setup_core.version_command(tmp_path, "0.4.0")]
    # written BEFORE starting: a version that crashes on start is still counted
    assert read_state(tmp_path)["pending_attempts"] == 1


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


# 「重新開啟」 (§3.7.6): the old helper is still ending when the launcher starts
# — its lock goes with its process, which still holds Chrome's profile. Asked
# to wait, the launcher waits for the lock rather than bringing a dying window
# forward, and only then starts the new version (review round 1).
def test_a_restarting_launcher_waits_for_the_old_helper_to_end(tmp_path):
    _installed(tmp_path, pending="0.4.0")
    running = setup_core.InstanceLock(tmp_path / "helper.lock")
    assert running.acquire()
    waited: list[float] = []

    def sleep(s: float) -> None:
        waited.append(s)
        if len(waited) == 2:
            running.release()  # the old process has ended

    started: list[list[str]] = []
    version = setup_core.launch(
        tmp_path, popen=lambda argv, cwd: started.append(argv), wait_s=15, sleep=sleep
    )

    assert version == "0.4.0" and len(started) == 1 and len(waited) == 2


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
    assert not (tmp_path / "outside-helper-uninstall.log").exists()


def test_what_the_clean_up_cannot_remove_is_written_down(tmp_path):
    home = tmp_path / "AIWorkspaceHelper"
    _installed(home)

    def stuck(path):
        raise PermissionError(13, "in use")

    left = setup_core.uninstall_main(
        home, run=_Run(), sleep=lambda s: None, log_dir=tmp_path, remove_tree=stuck
    )

    assert left == [str(home)]
    assert str(home) in (tmp_path / "outside-helper-uninstall.log").read_text(encoding="utf-8")


def _fail_fetch(_file: str) -> bytes:
    raise AssertionError("nothing should be downloaded")
