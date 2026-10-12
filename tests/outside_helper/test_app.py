"""The helper's window (docs/plan-outside-helper.md D13, §3.3): what the person
sees and what the buttons do. The window only draws `Controller.snapshot()`;
everything that decides is the controller, driven here one step at a time."""

from __future__ import annotations

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


def test_a_status_reads_with_or_without_its_detail():
    assert describe("paused", "") == "已暫停 — 卡片會改成自己查"
    assert describe("unreachable", "connection refused") == "連不到 AI 工作區:connection refused"
