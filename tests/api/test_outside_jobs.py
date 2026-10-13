"""The helper's jobs (docs/plan-outside-helper.md §3.2, P2).

The card hands a lookup to the person's own helper — a Python program on their
PC that drives Chrome — instead of the person fetching it by hand. The job lives
in the shared backend, so any pod can take the card's request and any pod can
answer the helper's poll.
"""

from __future__ import annotations

from tests.api.test_outside_answers import _asked


def _beat(client) -> None:
    r = client.post("/ai-workspace-helper/heartbeat", json={"version": "0.1.0", "protocol": 1})
    assert r.status_code == 204, r.text


def _hand(client, iid, call="c1", **body):
    return client.post(f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": call, **body})


def _claim(client):
    return client.post("/ai-workspace-helper/jobs/claim")


def test_the_card_hands_a_search_to_the_persons_helper():
    client, _spec, iid = _asked()
    _beat(client)

    r = _hand(client, iid, query="pandas 2.0 新功能 改過")

    assert r.status_code == 201, r.text
    job = _claim(client).json()
    assert job["kind"] == "search"
    # what the person edited on the card, not the AI's original (D4)
    assert job["args"] == "pandas 2.0 新功能 改過"
    assert job["id"] == r.json()["id"]


# D7: with no helper connected the card stays on the manual flow — a 「查」 nobody
# will act on is refused rather than queued.
def test_with_no_helper_connected_nothing_is_queued():
    client, _spec, iid = _asked()

    r = _hand(client, iid)

    assert r.status_code == 409, r.text
    _beat(client)
    assert _claim(client).status_code == 204


def test_a_helper_checked_in_too_long_ago_is_offline():
    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    t[0] += 31_000

    assert _hand(client, iid).status_code == 409
    assert client.get("/ai-workspace-helper/status").json() == {"online": False}


# A job belongs to the person who pressed 「查」; only their own helper takes it.
def test_only_the_persons_own_helper_takes_their_job():
    holder = {"id": "u"}
    client, _spec, iid = _asked(holder=holder)
    _beat(client)
    assert _hand(client, iid).status_code == 201

    holder["id"] = "someone-else"
    _beat(client)
    assert _claim(client).status_code == 204

    holder["id"] = "u"
    assert _claim(client).status_code == 200


def test_a_job_is_taken_once():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)

    assert _claim(client).status_code == 200
    assert _claim(client).status_code == 204


def test_an_answered_card_hands_nothing_over():
    from tests.api.test_outside_answers import _answer

    client, _spec, iid = _asked()
    _beat(client)
    assert _answer(client, iid, kind="not_found").status_code == 200

    assert _hand(client, iid).status_code == 409


def test_a_card_that_does_not_exist_hands_nothing_over():
    client, _spec, iid = _asked()
    _beat(client)

    assert _hand(client, iid, call="nope").status_code == 404


def _state(client, iid, call="c1"):
    r = client.get(f"/a/rca/items/{iid}/outside-jobs/{call}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_card_reads_how_its_job_is_going():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)
    assert _state(client, iid)["state"] == "queued"

    _claim(client)

    assert _state(client, iid)["state"] == "claimed"


def test_a_card_with_no_job_reads_as_none():
    client, _spec, iid = _asked()

    r = client.get(f"/a/rca/items/{iid}/outside-jobs/c1")

    assert r.status_code == 404


# The helper was closed or crashed after taking the job: it goes back to the
# queue rather than hanging the card forever.
def test_a_job_taken_and_never_finished_goes_back_to_the_queue():
    from workspace_app.api.outside_jobs import CLAIM_TIMEOUT_MS

    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    _hand(client, iid)
    assert _claim(client).status_code == 200

    t[0] += CLAIM_TIMEOUT_MS + 1
    _beat(client)

    assert _claim(client).status_code == 200


# The helper died mid-job (laptop closed): its claim lapses, and the card must
# read the job as waiting again — read as "claimed" it showed the helper at
# work forever, with no way back to the manual flow (review round 2).
def test_the_card_reads_a_lapsed_claim_as_waiting():
    from workspace_app.api.outside_jobs import CLAIM_TIMEOUT_MS

    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    _hand(client, iid)
    _claim(client)
    assert _state(client, iid)["state"] == "claimed"

    t[0] += CLAIM_TIMEOUT_MS + 1

    assert _state(client, iid)["state"] == "queued"


# A job its helper keeps taking and never finishes (the workspace full, a
# refusal that persists, a helper crashing on that page) ends, with why,
# after MAX_CLAIMS takes — never fetched again every two minutes (round 3).
def test_a_job_taken_too_often_ends_with_why():
    from workspace_app.api.outside_jobs import CLAIM_TIMEOUT_MS, MAX_CLAIMS

    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    _hand(client, iid)
    for _ in range(MAX_CLAIMS):
        assert _claim(client).status_code == 200
        t[0] += CLAIM_TIMEOUT_MS + 1
        _beat(client)

    assert _claim(client).status_code == 204
    job = _state(client, iid)
    assert job["state"] == "failed" and str(MAX_CLAIMS) in job["error"]


# On a shared item someone else's job — their helper gone — is not handed
# back to this person's 「查」: theirs would spin forever (round 3).
def test_a_lookup_is_not_handed_back_someone_elses_job():
    holder = {"id": "bob"}
    client, _spec, iid = _asked(holder=holder)
    _beat(client)
    bobs = _hand(client, iid).json()
    _claim(client)

    holder["id"] = "alice"
    _beat(client)
    mine = _hand(client, iid)

    assert mine.status_code == 201 and mine.json()["id"] == bobs["id"]
    assert _claim(client).status_code == 200  # alice's helper takes it now


def test_a_job_taken_recently_is_not_taken_again():
    from workspace_app.api.outside_jobs import CLAIM_TIMEOUT_MS

    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    _hand(client, iid)
    _claim(client)

    t[0] += CLAIM_TIMEOUT_MS - 1

    assert _claim(client).status_code == 204


# The helper could not do it (page not found, timed out, blocked): the card
# says why, and the person can retry or fall back to the manual flow.
def test_a_failure_reaches_the_card_with_its_reason():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]

    r = client.post(f"/ai-workspace-helper/jobs/{jid}/fail", json={"error": "page timed out"})

    assert r.status_code == 204, r.text
    assert _state(client, iid) | {"id": None} == {
        "id": None,
        "kind": "search",
        "args": "pandas 2.0 新功能",
        "state": "failed",
        "error": "page timed out",
        "note": "",
        "auto": False,
        "saved": "",
    }


def test_a_helper_cannot_fail_someone_elses_job():
    holder = {"id": "u"}
    client, _spec, iid = _asked(holder=holder)
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]

    holder["id"] = "someone-else"
    r = client.post(f"/ai-workspace-helper/jobs/{jid}/fail", json={"error": "x"})

    assert r.status_code == 404
    holder["id"] = "u"
    assert _state(client, iid)["state"] == "claimed"


def test_cancelling_takes_the_job_from_the_queue():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)

    r = client.delete(f"/a/rca/items/{iid}/outside-jobs/c1")

    assert r.status_code == 204, r.text
    assert _state(client, iid)["state"] == "cancelled"
    assert _claim(client).status_code == 204


# A job that has ended stays as it ended: a cancel after a failure does not
# rewrite the reason the card is showing.
def test_a_job_that_has_ended_cannot_be_cancelled():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]
    client.post(f"/ai-workspace-helper/jobs/{jid}/fail", json={"error": "page timed out"})

    r = client.delete(f"/a/rca/items/{iid}/outside-jobs/c1")

    assert r.status_code == 404
    assert _state(client, iid)["error"] == "page timed out"


# Handing the same card over again (「重查」 after a failure) replaces the job.
def test_handing_a_card_over_again_replaces_its_job():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]
    client.post(f"/ai-workspace-helper/jobs/{jid}/fail", json={"error": "x"})

    _hand(client, iid, query="again")

    job = _claim(client).json()
    assert (job["id"], job["args"], job["state"]) == (jid, "again", "claimed")


# The card's routes ask what answering the card by hand asks: someone who may
# not see the item learns nothing and changes nothing.
def test_someone_who_may_only_read_sees_the_job_but_cannot_hand_over_or_cancel():
    from tests.api.test_outside_answers import _as_carol

    client, _spec, iid = _as_carol()  # carol may read the item and its chat
    _beat(client)

    assert _hand(client, iid).status_code == 403
    assert client.delete(f"/a/rca/items/{iid}/outside-jobs/c1").status_code == 403
    assert client.get(f"/a/rca/items/{iid}/outside-jobs/c1").status_code == 404  # no job yet


def test_someone_with_no_access_to_the_item_learns_nothing_of_its_job():
    from tests.api.test_outside_answers import _app, _restricted_item

    holder = {"id": "bob"}
    client, spec = _app(holder)
    iid = _restricted_item(spec)  # bob's; nobody else may see it
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"}).status_code == 202
    _beat(client)
    assert _hand(client, iid).status_code == 201

    holder["id"] = "intruder"
    _beat(client)

    assert _hand(client, iid).status_code == 404
    assert client.get(f"/a/rca/items/{iid}/outside-jobs/c1").status_code == 404
    assert client.delete(f"/a/rca/items/{iid}/outside-jobs/c1").status_code == 404
    holder["id"] = "bob"
    assert _state(client, iid)["state"] == "queued"


# §3.4: a captcha stops the helper until the person solves it in the helper's
# Chrome window — the card says so, or the person is left watching 「正在查…」.
def test_the_helper_tells_the_card_it_is_waiting_on_the_person():
    client, _spec, iid = _asked()
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]

    r = client.post(f"/ai-workspace-helper/jobs/{jid}/note", json={"note": "captcha"})

    assert r.status_code == 204, r.text
    assert _state(client, iid)["note"] == "captcha"


# A person solving a captcha can take longer than a claim lasts; the job they
# are working on must not go back to the queue (and be fetched twice) meanwhile.
def test_a_job_waiting_on_the_person_is_not_taken_again():
    from workspace_app.api.outside_jobs import CLAIM_TIMEOUT_MS

    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]

    t[0] += CLAIM_TIMEOUT_MS - 1
    client.post(f"/ai-workspace-helper/jobs/{jid}/note", json={"note": "captcha"})
    t[0] += CLAIM_TIMEOUT_MS - 1
    _beat(client)

    assert _claim(client).status_code == 204


# A card reloaded mid-errand used to offer 「查」 again, and pressing it reset
# the helper's running job to `queued` — fetched twice, the first result then
# refused. A job under way is returned as it is (review round 1).
def test_handing_over_a_job_under_way_does_not_restart_it():
    client, _spec, iid = _asked()
    _beat(client)
    first = _hand(client, iid).json()
    _claim(client)

    again = _hand(client, iid)

    assert again.status_code == 201
    assert again.json()["id"] == first["id"]
    assert _state(client, iid)["state"] == "claimed"
    assert _claim(client).status_code == 204


# A helper waiting on the person is busy, not gone: what it says while it waits
# counts as checking in, so it still reads as connected (review round 1).
def test_a_helper_waiting_on_the_person_still_reads_as_connected():
    client, _spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]

    t[0] += 25_000
    client.post(f"/ai-workspace-helper/jobs/{jid}/note", json={"note": "captcha"})
    t[0] += 25_000

    assert client.get("/ai-workspace-helper/status").json() == {"online": True}
    # and what it last reported on its check-in is kept
    assert store.presence("u").version == "0.1.0"


def test_a_helper_cannot_note_someone_elses_job():
    holder = {"id": "u"}
    client, _spec, iid = _asked(holder=holder)
    _beat(client)
    _hand(client, iid)
    jid = _claim(client).json()["id"]

    holder["id"] = "someone-else"
    r = client.post(f"/ai-workspace-helper/jobs/{jid}/note", json={"note": "captcha"})

    assert r.status_code == 404
    holder["id"] = "u"
    assert _state(client, iid)["note"] == ""
