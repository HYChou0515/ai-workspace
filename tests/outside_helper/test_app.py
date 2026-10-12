"""The helper's window (docs/plan-outside-helper.md D13, §3.3): what the person
sees and what the buttons do. The window only draws `Controller.snapshot()`;
everything that decides is the controller, driven here one step at a time."""

from __future__ import annotations

import platform

from outside_helper.app import Controller, describe


class _Chrome:
    def __init__(self) -> None:
        self.logins = 0
        self.closed = False

    def open_login(self) -> None:
        self.logins += 1

    def close(self) -> None:
        self.closed = True


class _Worker:
    def __init__(self, statuses: list[str]) -> None:
        self._statuses = iter(statuses)
        self.paused = False
        self.status = "starting"
        self.detail = ""
        self.ticks = 0
        self.manifest = None

    def tick(self) -> None:
        self.ticks += 1
        self.status = "paused" if self.paused else next(self._statuses)


def _controller(statuses, chrome=None):
    chrome = chrome or _Chrome()
    worker = _Worker(statuses)
    ctl = Controller(lambda: chrome, lambda _chrome: worker)
    ctl.open()
    return ctl, chrome, worker


# §3.3: the login expired — bring Chrome forward on the app ONCE, and let the
# person sign in; not a new tab every couple of seconds while they type.
def test_an_expired_login_opens_the_login_once():
    ctl, chrome, _worker = _controller(["needs_login", "needs_login", "connected", "needs_login"])

    for _ in range(4):
        ctl.step()

    assert chrome.logins == 2  # once per time it expired


def test_the_login_button_opens_the_login():
    ctl, chrome, _worker = _controller(["connected", "connected"])

    ctl.send("login")
    ctl.step()

    assert chrome.logins == 1


def test_pause_and_resume():
    ctl, _chrome, worker = _controller(["connected"])

    ctl.send("pause")
    ctl.step()
    assert ctl.snapshot()[0] == "paused"

    ctl.send("resume")
    ctl.step()
    assert ctl.snapshot()[0] == "connected"
    assert not worker.paused


def test_quitting_closes_chrome_and_stops():
    ctl, chrome, worker = _controller(["connected"])

    ctl.send("quit")

    assert ctl.step() is False
    assert chrome.closed and worker.ticks == 0


# Chrome not installed, or its profile held by a helper still shutting down:
# the window says so instead of the helper vanishing.
def test_a_chrome_that_will_not_start_is_shown_not_a_crash():
    def broken():
        raise RuntimeError("Chromium distribution 'chrome' is not found")

    ctl = Controller(broken, lambda _c: _Worker([]))
    ctl.open()

    status, detail = ctl.snapshot()
    assert status == "no_chrome" and "not found" in detail
    assert ctl.step() is True  # still running: the person can read it and quit


def test_every_status_reads_as_a_sentence():
    for status in (
        "starting",
        "connected",
        "paused",
        "needs_login",
        "update_required",
        "unreachable",
        "error",
        "no_chrome",
    ):
        text = describe(status, "x")
        assert text and status not in text
    assert describe("connected", "pandas 2.0") == "已連線 — 正在查:pandas 2.0"


def test_the_thread_polls_until_told_to_quit():
    chrome = _Chrome()
    ctl: Controller

    class _Quits(_Worker):
        def tick(self) -> None:
            super().tick()
            if self.ticks == 3:
                ctl.send("quit")

    worker = _Quits(["connected"] * 3)
    ctl = Controller(lambda: chrome, lambda _c: worker, poll_s=0.01)

    ctl.run()

    assert (worker.ticks, chrome.closed) == (3, True)


def test_with_no_chrome_the_buttons_do_nothing_but_quit():
    def broken():
        raise RuntimeError("")

    ctl = Controller(broken, lambda _c: _Worker([]))
    ctl.open()
    assert ctl.snapshot() == ("no_chrome", "RuntimeError")

    for command in ("login", "pause", "resume"):
        ctl.send(command)
    assert ctl.step() is True
    ctl.send("quit")
    assert ctl.step() is False


def test_a_chrome_that_fails_to_close_still_lets_the_helper_end():
    class _Stuck(_Chrome):
        def close(self) -> None:
            raise RuntimeError("Target closed")

    ctl, _chrome, _worker = _controller(["connected"], chrome=_Stuck())

    ctl.send("quit")

    assert ctl.step() is False


class _Updater:
    def __init__(self, fails: bool = False) -> None:
        self.calls: list[object] = []
        self.status = ""
        self.fails = fails

    def connected(self) -> None:
        self.calls.append("connected")

    def tick(self, required=None) -> None:
        self.calls.append(("tick", required))
        if self.fails:
            raise RuntimeError("manifest unreadable")


def _with_updater(statuses, updater, manifest=None):
    chrome, worker = _Chrome(), _Worker(statuses)
    worker.manifest = manifest
    ctl = Controller(lambda: chrome, lambda _c: worker, make_updater=lambda _c: updater)
    ctl.open()
    return ctl, chrome


# §3.7.5 step 3 / §3.7.4: once connected, the version confirms itself and the
# scheduled check runs, on the same thread as the jobs.
def test_once_connected_the_update_check_runs():
    up = _Updater()
    ctl, _chrome = _with_updater(["connected"], up)

    ctl.step()

    assert up.calls == ["connected", ("tick", None)]


# §3.7.6: refused, the helper updates from the release the refusal named.
def test_a_refused_helper_updates_from_the_release_it_was_given():
    up = _Updater()
    release = {"version": "0.4.0", "file": "outside-helper-0.4.0.zip"}
    ctl, _chrome = _with_updater(["update_required"], up, manifest=release)

    ctl.step()

    assert up.calls == [("tick", release)]
    assert ctl.update_status() == ""


def test_signed_out_it_does_not_look_for_updates():
    up = _Updater()
    ctl, _chrome = _with_updater(["needs_login"], up)

    ctl.step()

    assert up.calls == []


def test_an_update_check_that_fails_does_not_stop_the_helper():
    ctl, _chrome = _with_updater(["connected", "connected"], _Updater(fails=True))

    assert ctl.step() is True
    assert ctl.step() is True


# §3.7.6: 「重新開啟」 — end, so the launcher can start the version now ready.
def test_restart_ends_the_helper_and_asks_for_the_launcher():
    ctl, chrome = _with_updater(["connected"], _Updater())

    ctl.send("restart")

    assert ctl.step() is False
    assert chrome.closed and ctl.restart


# §3.7.4 step 4: a freshly installed version proves it can drive Chrome before
# it counts as installed — Playwright imports and its driver is on disk (an
# antivirus that removed node.exe is caught here, not at the first lookup).
def test_the_self_test_passes_on_a_working_install(capsys):
    from outside_helper.app import self_test
    from outside_helper.version import __version__

    assert self_test() == 0
    assert __version__ in capsys.readouterr().out


def test_the_self_test_fails_when_the_driver_is_gone(monkeypatch, tmp_path, capsys):
    from outside_helper import app

    monkeypatch.setattr(
        app, "_driver_paths", lambda: (str(tmp_path / "node.exe"), str(tmp_path / "cli.js"))
    )

    assert app.self_test() == 1
    assert "node.exe" in capsys.readouterr().out


# The helper as the window runs it — Backend, Worker, Updater, the check-in's
# report — wired once, here, for the window and for the live check alike.
def test_the_helper_is_wired_to_report_what_it_runs_on(tmp_path):
    from outside_helper.app import make_controller

    from tests.api.test_outside_answers import _asked
    from tests.outside_helper.test_worker import _backend

    client, _spec, _iid = _asked()
    transport = _backend(client)

    class _Chrome:
        def send(self, *a):
            return transport._send(*a)

        def fetch_bytes(self, *a):
            return transport._fetch_bytes(*a)  # ty: ignore[call-non-callable]

        def version(self) -> str:
            return "141.0.7390.55"

        def close(self) -> None:
            pass

    ctl = make_controller(tmp_path, "http://app", _Chrome)
    ctl.open()
    ctl.step()

    assert ctl.snapshot()[0] == "connected"
    seen = client.app.state.outside_jobs.presence("u")
    assert (seen.chrome, seen.python) == ("141.0.7390.55", platform.python_version())


# What the window says about the next version (§3.7.4–§3.7.6).
def test_the_update_line_says_where_the_next_version_stands():
    from outside_helper.app import describe_update

    assert describe_update("", forced=False) == ""
    assert describe_update("preparing", forced=False) == "正在準備新版…"
    assert describe_update("ready", forced=False) == "新版已備好,下次開啟生效"
    assert describe_update("ready", forced=True) == "新版已備好,按「重新開啟」換上"
    # the running version carries on — unless it is refused, when only a
    # reinstall from the download page is left (§3.7.6)
    assert describe_update("failed: sha256", forced=False) == "新版準備失敗:sha256(這一版照常使用)"
    assert "外網小幫手" in describe_update("failed: sha256", forced=True)


def test_a_status_reads_with_or_without_its_detail():
    assert describe("paused", "") == "已暫停 — 卡片會改成自己查"
    assert describe("unreachable", "connection refused") == "連不到 AI 工作區:connection refused"
