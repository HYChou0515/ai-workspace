"""The running helper keeps itself current (docs/plan-outside-helper.md
§3.7.4, §3.7.6): it checks at start and every six hours, fetches through its
own signed-in line, prepares in the background — never in the way of the
person's lookups — and a version the backend refuses is updated at once."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ai_workspace.setup_core import read_state, write_state
from ai_workspace.updates import CHECK_EVERY_S, RETRY_AFTER_S, Updater

from tests.ai_workspace.test_setup_core import _Run
from tests.ai_workspace.test_updates import _zip


class _Backend:
    def __init__(self, manifest: dict | None, data: bytes = b"") -> None:
        self._manifest = manifest
        self._data = data
        self.manifests = 0
        self.downloads: list[str] = []

    def manifest(self):
        self.manifests += 1
        return self._manifest

    def download(self, file: str) -> bytes:
        self.downloads.append(file)
        return self._data


def _release(version: str) -> tuple[dict, bytes]:
    data = _zip(version)
    return (
        {
            "version": version,
            "min_version": "0.0.0",
            "file": f"ai-workspace-{version}.zip",
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        data,
    )


def _home(tmp_path: Path, current: str = "0.3.0") -> Path:
    (tmp_path / "versions" / current / "venv").mkdir(parents=True)
    write_state(tmp_path, {"base_python": "/py/pythonw", "current": current, "bad": []})
    return tmp_path


def _updater(home, backend, clock, run=None, version="0.3.0"):
    return Updater(
        home,
        backend,
        clock=lambda: clock[0],
        spawn=lambda work: work(),  # the background thread, run inline
        run=run or _Run(),
        version=version,
    )


def test_a_newer_release_is_prepared_for_the_next_start(tmp_path):
    manifest, data = _release("0.4.0")
    backend, clock = _Backend(manifest, data), [0.0]
    up = _updater(_home(tmp_path), backend, clock)

    up.tick()

    assert backend.downloads == ["ai-workspace-0.4.0.zip"]
    assert read_state(tmp_path)["pending"] == "0.4.0"
    assert up.status == "ready"


def test_it_checks_at_start_then_every_six_hours(tmp_path):
    backend, clock = _Backend(None), [0.0]
    up = _updater(_home(tmp_path), backend, clock)

    up.tick()
    clock[0] = CHECK_EVERY_S - 1
    up.tick()
    clock[0] = CHECK_EVERY_S
    up.tick()

    assert backend.manifests == 2


def test_nothing_newer_fetches_nothing(tmp_path):
    manifest, data = _release("0.3.0")
    backend, clock = _Backend(manifest, data), [0.0]
    up = _updater(_home(tmp_path), backend, clock)

    up.tick()

    assert backend.downloads == []
    assert up.status == ""


def test_a_failed_preparation_is_shown_and_the_running_version_carries_on(tmp_path):
    manifest, data = _release("0.4.0")
    backend, clock = _Backend(manifest, data), [0.0]
    up = _updater(_home(tmp_path), backend, clock, run=_Run("--self-test"))

    up.tick()

    assert up.status.startswith("failed")
    assert read_state(tmp_path)["current"] == "0.3.0"
    assert read_state(tmp_path).get("pending") is None


# §3.7.6: refused by the backend, the helper updates at once — from the release
# the refusal named — and does not hammer the backend when that fails.
def test_a_refused_version_updates_at_once_from_the_release_it_was_given(tmp_path):
    manifest, data = _release("0.4.0")
    backend, clock = _Backend(None, data), [0.0]
    up = _updater(_home(tmp_path), backend, clock)
    up.tick()  # the scheduled check, nothing published then
    clock[0] = 10.0

    up.tick(required=manifest)

    assert backend.downloads == ["ai-workspace-0.4.0.zip"]
    assert up.status == "ready"


# Refused, and the release it must update to already failed here three times:
# it will not be tried again, so the window must say what is left — a
# reinstall — rather than nothing at all (review round 1).
def test_a_refused_helper_whose_update_is_marked_bad_says_to_reinstall(tmp_path):
    manifest, data = _release("0.4.0")
    home = _home(tmp_path)
    write_state(home, {**read_state(home), "bad": ["0.4.0"]})
    backend = _Backend(None, data)
    up = _updater(home, backend, [0.0])

    up.tick(required=manifest)

    assert backend.downloads == []
    assert up.status.startswith("failed")


# A manifest's version names folders on the PC (versions/<v>): only a version.
def test_a_release_whose_version_is_not_a_version_is_not_installed(tmp_path):
    manifest, data = _release("0.4.0")
    manifest["version"] = "../../0.9.0"
    backend = _Backend(manifest, data)
    up = _updater(_home(tmp_path), backend, [0.0])

    up.tick()

    assert backend.downloads == []
    assert not (tmp_path.parent / "0.9.0").exists()


def test_a_forced_update_that_failed_waits_before_trying_again(tmp_path):
    manifest, data = _release("0.4.0")
    backend, clock = _Backend(None, data), [0.0]
    up = _updater(_home(tmp_path), backend, clock, run=_Run("--self-test"))

    up.tick(required=manifest)
    clock[0] = RETRY_AFTER_S - 1
    up.tick(required=manifest)
    clock[0] = RETRY_AFTER_S
    up.tick(required=manifest)

    assert len(backend.downloads) == 2


# §3.7.5 step 3: the waiting version, once up, makes itself current — once.
def test_the_new_version_confirms_itself_once_connected(tmp_path):
    home = _home(tmp_path)
    (home / "versions" / "0.4.0" / "venv").mkdir(parents=True)
    state = read_state(home)
    write_state(home, {**state, "pending": "0.4.0", "pending_attempts": 1})
    up = _updater(home, _Backend(None), [0.0], version="0.4.0")

    up.connected()
    up.connected()

    assert read_state(home)["current"] == "0.4.0"
    assert read_state(home)["previous"] == "0.3.0"


# Asked once per run, not on every poll: the check-in happens every couple of
# seconds, and there is nothing to confirm after the first.
def test_it_asks_to_be_confirmed_once_per_run(tmp_path, monkeypatch):
    from ai_workspace import setup_core

    asked: list[str] = []
    monkeypatch.setattr(setup_core, "confirm", lambda home, v: asked.append(v) or False)
    up = _updater(_home(tmp_path), _Backend(None), [0.0])

    for _ in range(5):
        up.connected()

    assert asked == ["0.3.0"]


def test_what_it_reports_on_each_check_in(tmp_path):
    home = _home(tmp_path)
    write_state(home, {**read_state(home), "update": "0.4.0 起不來,已退回 0.3.0"})

    assert _updater(home, _Backend(None), [0.0]).report() == "0.4.0 起不來,已退回 0.3.0"
