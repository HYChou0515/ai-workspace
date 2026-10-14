"""The helper says when a newer version is out (docs/plan-outside-helper.md
D21, §3.7.4, §3.7.6): it reads the published release at start and every six
hours, and only tells. It downloads and installs nothing — its line to AI
Workspace does not verify the certificate (D20), so the person downloads the
zip in their own browser and runs install.pyw again."""

from __future__ import annotations

import pytest
from ai_workspace_helper.updates import CHECK_EVERY_S, Updater


class _Backend:
    """The release info, and nothing else: an updater that asks this backend
    for anything but the manifest (a download) fails the test."""

    def __init__(self, manifest: dict | None) -> None:
        self._manifest = manifest
        self.manifests = 0

    def manifest(self):
        self.manifests += 1
        return self._manifest

    def __getattr__(self, name: str):
        raise AssertionError(f"the updater asked the backend for {name!r}")


def _release(version: str) -> dict:
    return {
        "version": version,
        "min_version": "0.0.0",
        "file": f"ai-workspace-helper-{version}.zip",
        "sha256": "0" * 64,
    }


def _updater(backend, clock, version="0.3.0"):
    return Updater(backend, clock=lambda: clock[0], version=version)


def test_a_newer_release_is_told_about_and_nothing_is_fetched():
    up = _updater(_Backend(_release("0.4.0")), [0.0])

    up.tick()

    assert up.status == "available: 0.4.0"


def test_it_checks_at_start_then_every_six_hours():
    backend, clock = _Backend(_release("0.4.0")), [0.0]
    up = _updater(backend, clock)

    up.tick()
    clock[0] = CHECK_EVERY_S - 1
    up.tick()
    assert backend.manifests == 1
    clock[0] = CHECK_EVERY_S
    up.tick()
    assert backend.manifests == 2


@pytest.mark.parametrize("published", ["0.3.0", "0.2.9", "not-a-version"])
def test_a_release_no_newer_than_this_one_says_nothing(published):
    up = _updater(_Backend(_release(published)), [0.0])

    up.tick()

    assert up.status == ""


def test_with_nothing_published_it_says_nothing():
    up = _updater(_Backend(None), [0.0])

    up.tick()

    assert up.status == ""


# The operator took the newer release back: the line goes with it.
def test_a_release_taken_back_takes_its_line_with_it():
    backend, clock = _Backend(_release("0.4.0")), [0.0]
    up = _updater(backend, clock)
    up.tick()

    backend._manifest = _release("0.3.0")
    clock[0] = CHECK_EVERY_S
    up.tick()

    assert up.status == ""


# §3.7.6: refused (426), the helper says which release to install — the one
# the refusal named — at once, without asking for the manifest.
def test_a_refused_version_says_which_release_to_install():
    backend = _Backend(None)
    up = _updater(backend, [0.0])

    up.tick(required=_release("0.4.0"))

    assert up.status == "required: 0.4.0"
    assert backend.manifests == 0


def test_a_refusal_that_names_no_release_still_says_to_reinstall():
    up = _updater(_Backend(None), [0.0])

    up.tick(required={})

    assert up.status == "required: "


# Refused, then let back in (the operator lowered min_version): the line must
# not keep saying 「請到「小幫手」頁下載新版」 until the next six-hour check —
# connected again, it reads the release at once (review of D21).
def test_connected_again_after_a_refusal_it_reads_the_release_at_once():
    backend, clock = _Backend(_release("0.3.0")), [0.0]
    up = _updater(backend, clock)
    up.tick()
    clock[0] = 60
    up.tick(required=_release("0.4.0"))
    assert up.status == "required: 0.4.0"

    clock[0] = 120
    up.tick()

    assert backend.manifests == 2
    assert up.status == ""


# Whatever came down the unverified line (D20) in a 426's body, a release that
# is not one names no version — it is not a reason to fail every poll.
@pytest.mark.parametrize("named", ["x", ["0.4.0"], {"version": 4}])
def test_a_refusal_naming_something_else_still_says_to_reinstall(named):
    up = _updater(_Backend(None), [0.0])

    up.tick(required=named)

    assert up.status == ("required: 4" if isinstance(named, dict) else "required: ")
