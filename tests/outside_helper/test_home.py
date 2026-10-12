"""The helper's own folder (docs/plan-outside-helper.md §3.7.1): one helper at a
time, and settings that are never left half-written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from outside_helper.home import (
    InstanceLock,
    profile_dir,
    read_settings,
    root,
    write_settings,
)


# One Chrome profile cannot be opened twice: started at login AND double-clicked,
# the second helper must find out before it touches the profile (§3.7 table).
def test_only_one_helper_runs_at_a_time(tmp_path):
    first = InstanceLock(tmp_path / "helper.lock")
    second = InstanceLock(tmp_path / "helper.lock")

    assert first.acquire()
    assert not second.acquire()

    first.release()
    assert second.acquire()
    second.release()
    second.release()  # ending twice (window closed, then quit) is harmless


# 「重設登入」 (§3.7.7): the profile is moved aside, not deleted — a reset by
# mistake loses nothing, and the next Chrome starts on a fresh one.
def test_resetting_the_login_moves_the_profile_aside(tmp_path):
    from outside_helper.home import reset_profile

    (profile_dir(tmp_path) / "Default").mkdir(parents=True)
    (profile_dir(tmp_path) / "Default" / "Cookies").write_text("sid")

    reset_profile(tmp_path)

    assert not profile_dir(tmp_path).exists()
    [aside] = [p for p in tmp_path.iterdir() if p.name.startswith("profile.old-")]
    assert (aside / "Default" / "Cookies").read_text() == "sid"
    reset_profile(tmp_path)  # nothing there: nothing to do


def test_settings_are_kept_between_starts(tmp_path):
    write_settings(tmp_path, {"app_url": "https://ai.example.corp"})

    assert read_settings(tmp_path) == {"app_url": "https://ai.example.corp"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


# Never written, or unreadable (cut off by a crash before the atomic writer
# existed, edited by hand): the helper asks again rather than failing to start.
def test_settings_that_cannot_be_read_are_asked_for_again(tmp_path):
    assert read_settings(tmp_path) == {}

    (tmp_path / "settings.json").write_text('{"app_url": ')
    assert read_settings(tmp_path) == {}

    (tmp_path / "settings.json").write_text(json.dumps(["not", "an", "object"]))
    assert read_settings(tmp_path) == {}


def test_the_folder_is_the_override_then_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTSIDE_HELPER_HOME", str(tmp_path / "dev"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert root() == tmp_path / "dev"

    monkeypatch.delenv("OUTSIDE_HELPER_HOME")
    assert root() == tmp_path / "AppData" / "Local" / "OutsideHelper"
    # the profile sits beside the versions, so an update keeps the login
    assert profile_dir(root()) == root() / "profile"

    monkeypatch.delenv("LOCALAPPDATA")
    assert root() == Path.home() / ".local" / "share" / "outside-helper"


def test_settings_that_cannot_be_written_leave_the_old_ones(tmp_path):
    write_settings(tmp_path, {"app_url": "https://old.example"})

    with pytest.raises(TypeError):
        write_settings(tmp_path, {"app_url": object()})

    assert read_settings(tmp_path) == {"app_url": "https://old.example"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
