"""The helper's loop (docs/plan-outside-helper.md §3.4, P5), against the REAL
backend routes: check in, take the person's job, run its errand in the browser,
hand the page back — or say why it could not.

The browser here is a stand-in (the real one is exercised in P6's integration
tests); the backend is the app itself, through the same HTTP the helper speaks.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_workspace_helper.backend import Backend
from ai_workspace_helper.errands import FetchedPage
from ai_workspace_helper.version import PROTOCOL, __version__
from ai_workspace_helper.worker import Worker
from starlette.testclient import TestClient as RawClient

from tests.api.test_outside_answers import _answers, _asked


def _backend(client) -> Backend:
    """The helper's backend client over the app's own HTTP (raw: the helper
    speaks the real `/api/…` paths)."""
    raw = RawClient(client.app)

    def send(method: str, path: str, body: dict | None, headers: dict[str, str]):
        r = raw.request(method, path, json=body, headers=headers)
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = None
        return r.status_code, data

    def fetch_bytes(path: str, headers: dict[str, str]):
        r = raw.get(path, headers=headers)
        return r.status_code, r.content

    return Backend(send, fetch_bytes=fetch_bytes)


@dataclass
class _Browser:
    """Stands in for Chrome: each errand gets `page`, records what it was asked."""

    asked: list[tuple[str, str]]

    def run(self, errand, args: str, note, where=None) -> FetchedPage:
        self.note = note
        self.where = where or (lambda _site: None)
        return errand(self, args)


def _errands(**over):
    def search(browser, query: str) -> FetchedPage:
        browser.asked.append(("search", query))
        return FetchedPage(
            title="results",
            url=f"https://www.google.com/search?q={query}",
            markdown="- [a](https://a.example/)",
        )

    return {"search": search, **over}


def _hand(client, iid, **body):
    r = client.post(f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": "c1", **body})
    assert r.status_code == 201, r.text


def test_the_helper_takes_the_job_fetches_and_the_card_is_answered():
    client, spec, iid = _asked()
    browser = _Browser(asked=[])
    worker = Worker(
        _backend(client), browser, errands=_errands(), date=lambda: "2031-01-02"
    )  # not today: the backend would fill today in
    worker.tick()  # checks in: the card may now hand over
    _hand(client, iid, query="pandas 2.0 新功能")

    worker.tick()

    assert browser.asked == [("search", "pandas 2.0 新功能")]
    [answer] = _answers(spec)
    assert "lookups/2031-01-02-" in answer.content
    assert worker.status == "connected"


def test_an_errand_that_fails_reports_why_and_the_card_shows_it():
    client, spec, iid = _asked()

    def broken(browser, query):
        raise RuntimeError("page timed out")

    worker = Worker(_backend(client), _Browser([]), errands=_errands(search=broken))
    worker.tick()
    _hand(client, iid)

    worker.tick()

    job = client.get(f"/a/rca/items/{iid}/outside-jobs/c1").json()
    assert (job["state"], job["error"]) == ("failed", "page timed out")
    assert _answers(spec) == []


def test_a_kind_this_helper_cannot_do_says_so():
    client, _spec, iid = _asked()
    worker = Worker(_backend(client), _Browser([]), errands={})
    worker.tick()
    _hand(client, iid)

    worker.tick()

    job = client.get(f"/a/rca/items/{iid}/outside-jobs/c1").json()
    assert job["state"] == "failed"
    assert "search" in job["error"] and "update" in job["error"]


def test_the_helper_says_which_version_it_is():
    client, _spec, _iid = _asked()
    seen: list[dict[str, str]] = []
    backend = _backend(client)
    inner = backend._send

    def spy(method, path, body, headers):
        seen.append(headers)
        return inner(method, path, body, headers)

    backend._send = spy
    Worker(backend, _Browser([]), errands={}).tick()

    assert seen and all(
        h["X-AI-Workspace-Helper-Version"] == __version__
        and h["X-AI-Workspace-Helper-Protocol"] == str(PROTOCOL)
        for h in seen
    )


# Paused: the helper takes nothing — and does not check in either, so the card
# offers the manual flow rather than a 「查」 nobody will act on (D7).
def test_a_paused_helper_takes_nothing_and_is_offline():
    client, _spec, iid = _asked()
    worker = Worker(_backend(client), _Browser([]), errands=_errands())
    worker.paused = True

    worker.tick()

    assert client.get("/ai-workspace-helper/status").json() == {"online": False}
    assert worker.status == "paused"


# Signed out (the deploy's login expired): say so, take nothing, keep trying.
def test_a_signed_out_helper_asks_for_the_login():
    worker = Worker(Backend(lambda m, p, b, h: (401, None)), _Browser([]), errands={})

    worker.tick()

    assert worker.status == "needs_login"


def test_a_backend_that_cannot_be_reached_reads_as_offline_not_a_crash():
    def down(method, path, body, headers):
        raise OSError("connection refused")

    worker = Worker(Backend(down), _Browser([]), errands={})

    worker.tick()

    assert worker.status == "unreachable"


# A captcha: the errand says it is waiting on the person, and the card shows it
# while the job is still under way (§3.4).
def test_what_the_errand_waits_on_reaches_the_card_while_it_runs():
    client, _spec, iid = _asked()
    during: list[str] = []

    def search(browser, query):
        browser.note("captcha")
        during.append(client.get(f"/a/rca/items/{iid}/outside-jobs/c1").json()["note"])
        return FetchedPage(title="t", url="https://www.google.com/search?q=x", markdown="ok")

    worker = Worker(_backend(client), _Browser([]), errands=_errands(search=search))
    worker.tick()
    _hand(client, iid)

    worker.tick()

    assert during == ["captcha"]


# 403 is "not allowed", not "signed out": signing in again changes nothing, so
# the helper must not keep popping the login page at the person (review r1).
def test_a_refusal_is_not_taken_for_an_expired_login():
    worker = Worker(
        Backend(lambda m, p, b, h: (403, {"detail": "you may not converse here"})),
        _Browser([]),
        errands={},
    )

    worker.tick()

    assert (worker.status, worker.detail) == ("error", "you may not converse here")


# Behind a company gateway an expired login is usually not a 401 but a redirect
# to the SSO page; followed, that is a 200 login page the helper would take for
# a check-in. The helper does not follow redirects, and a 3xx means: sign in.
def test_a_redirect_to_the_login_page_asks_for_the_login():
    worker = Worker(Backend(lambda m, p, b, h: (302, None)), _Browser([]), errands={})

    worker.tick()

    assert worker.status == "needs_login"


# §3.7.6: below the minimum version the backend refuses with the manifest of
# the current one; the helper stops taking jobs and knows what to fetch.
def test_a_version_no_longer_accepted_says_an_update_is_needed(tmp_path):
    from tests.api.test_ai_workspace_helper_release import _publish

    manifest, _ = _publish(tmp_path, "0.9.0", min_version="0.9.0")
    client, _spec, _iid = _asked(ai_workspace_helper_dir=str(tmp_path))
    worker = Worker(_backend(client), _Browser([]), errands={})

    worker.tick()

    assert worker.status == "update_required"
    assert worker.manifest == {**manifest, "size": len(b"zip of 0.9.0")}


# §3.7.4: the helper reads the published release and fetches its zip through
# the same signed-in line it takes jobs through.
def test_the_helper_reads_and_fetches_the_published_release(tmp_path):
    from tests.api.test_ai_workspace_helper_release import _publish

    manifest, zip_bytes = _publish(tmp_path, "0.9.0")
    client, _spec, _iid = _asked(ai_workspace_helper_dir=str(tmp_path))
    backend = _backend(client)

    release = backend.manifest()
    assert release is not None and release["version"] == "0.9.0"
    assert backend.download(manifest["file"]) == zip_bytes


def test_with_nothing_published_there_is_no_release():
    client, _spec, _iid = _asked()

    assert _backend(client).manifest() is None


# §3.7.7: each check-in says what the helper runs on.
def test_the_check_in_says_what_the_helper_runs_on():
    client, _spec, _iid = _asked()
    info = {"chrome": "141.0", "playwright": "1.63.0", "python": "3.12.5", "update": ""}
    worker = Worker(_backend(client), _Browser([]), errands={}, info=lambda: info)

    worker.tick()

    seen = client.app.state.outside_jobs.presence("u")
    assert (seen.version, seen.chrome, seen.playwright, seen.python) == (
        __version__,
        "141.0",
        "1.63.0",
        "3.12.5",
    )


def test_any_other_refusal_shows_the_backends_reason():
    worker = Worker(
        Backend(lambda m, p, b, h: (500, {"detail": "store unavailable"})), _Browser([]), errands={}
    )

    worker.tick()

    assert (worker.status, worker.detail) == ("error", "store unavailable")


# The login expires while a page is being fetched: that is not the page's
# failure — the job is left to come back once the person has signed in again.
def test_a_login_that_expires_mid_errand_does_not_fail_the_job():
    client, _spec, iid = _asked()
    backend = _backend(client)
    inner = backend._send
    expired = [False]

    paths: list[str] = []

    def send(method, path, body, headers):
        paths.append(path)
        return (401, None) if expired[0] else inner(method, path, body, headers)

    backend._send = send

    def search(browser, query):
        expired[0] = True
        browser.note("captcha")
        raise AssertionError("unreachable: the note above signs out")

    worker = Worker(backend, _Browser([]), errands=_errands(search=search))
    worker.tick()
    _hand(client, iid)

    worker.tick()

    assert worker.status == "needs_login"
    # it does not even try to report the job failed
    assert not any(p.endswith("/fail") for p in paths)
    expired[0] = False
    assert client.get(f"/a/rca/items/{iid}/outside-jobs/c1").json()["state"] == "claimed"


# A search goes where the card's own buttons go — the deploy's
# `server.lookup_targets`, read from the backend, not a list of the helper's.
def test_the_helper_searches_where_the_deploy_says():
    targets = [{"name": "Corp search", "url": "https://search.corp/?q={q}"}]
    client, _spec, _iid = _asked(lookup_targets=targets)

    assert _backend(client).lookup_targets() == targets


# The window's 查詢紀錄 (D13): each job the helper ran leaves one line — what,
# on which site, and how it ended — and the log says the same.
def test_each_job_leaves_a_line_in_the_history_and_the_log(caplog):
    import logging

    from ai_workspace_helper.errands import ErrandFailed
    from ai_workspace_helper.history import History

    def found(browser, query: str) -> FetchedPage:
        browser.where("Google")
        return FetchedPage(title="r", url="https://www.google.com/search?q=x", markdown="ok")

    def not_found(browser, query: str) -> FetchedPage:
        browser.where("Google")
        raise ErrandFailed("the page answered 404")

    history = History(None)
    for errand, query in ((found, "pandas 2.0"), (not_found, "numpy 3")):
        client, _spec, iid = _asked()
        worker = Worker(_backend(client), _Browser([]), errands={"search": errand}, history=history)
        worker.tick()
        _hand(client, iid, query=query)
        with caplog.at_level(logging.INFO, logger="ai_workspace_helper.worker"):
            worker.tick()

    rows = [(r["kind"], r["args"], r["site"], r["outcome"], r["reason"]) for r in history.recent()]
    assert rows == [
        ("search", "numpy 3", "Google", "failed", "the page answered 404"),
        ("search", "pandas 2.0", "Google", "done", ""),
    ]
    assert "search 'pandas 2.0'" in caplog.text and "done" in caplog.text
