"""The helper's loop (docs/plan-outside-helper.md §3.4, P5), against the REAL
backend routes: check in, take the person's job, run its errand in the browser,
hand the page back — or say why it could not.

The browser here is a stand-in (the real one is exercised in P6's integration
tests); the backend is the app itself, through the same HTTP the helper speaks.
"""

from __future__ import annotations

from dataclasses import dataclass

from outside_helper.backend import Backend
from outside_helper.errands import FetchedPage
from outside_helper.version import PROTOCOL, __version__
from outside_helper.worker import Worker
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

    return Backend(send)


@dataclass
class _Browser:
    """Stands in for Chrome: each errand gets `page`, records what it was asked."""

    asked: list[tuple[str, str]]

    def run(self, errand, args: str, note) -> FetchedPage:
        self.note = note
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
        h["X-Outside-Helper-Version"] == __version__
        and h["X-Outside-Helper-Protocol"] == str(PROTOCOL)
        for h in seen
    )


# Paused: the helper takes nothing — and does not check in either, so the card
# offers the manual flow rather than a 「查」 nobody will act on (D7).
def test_a_paused_helper_takes_nothing_and_is_offline():
    client, _spec, iid = _asked()
    worker = Worker(_backend(client), _Browser([]), errands=_errands())
    worker.paused = True

    worker.tick()

    assert client.get("/outside-helper/status").json() == {"online": False}
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


# Behind a company gateway an expired login is usually not a 401 but a redirect
# to the SSO page; followed, that is a 200 login page the helper would take for
# a check-in. The helper does not follow redirects, and a 3xx means: sign in.
def test_a_redirect_to_the_login_page_asks_for_the_login():
    worker = Worker(Backend(lambda m, p, b, h: (302, None)), _Browser([]), errands={})

    worker.tick()

    assert worker.status == "needs_login"


# §3.7.6: below the minimum version the backend refuses with the manifest of
# the current one; the helper stops taking jobs and knows what to fetch.
def test_a_version_no_longer_accepted_says_an_update_is_needed():
    manifest = {"version": "0.2.0", "min_version": "0.2.0", "file": "outside-helper-0.2.0.zip"}
    worker = Worker(Backend(lambda m, p, b, h: (426, manifest)), _Browser([]), errands={})

    worker.tick()

    assert (worker.status, worker.manifest) == ("update_required", manifest)


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
