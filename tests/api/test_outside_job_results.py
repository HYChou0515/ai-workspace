"""The helper's result goes to the AI (docs/plan-outside-helper.md §3.2, P3).

What the helper fetched is saved and sent exactly as a person's paste is — the
same code (`outside_lookup_routes`) writes `lookups/` and sends the message that
answers the card — so the AI reads one shape whichever way the page arrived.
And the links in anything a lookup brought back are remembered for the chat, so
a later open-URL card for one of them needs no nod (D12).
"""

from __future__ import annotations

from tests.api.test_outside_answers import _answer, _answers, _read
from tests.api.test_outside_jobs import _beat, _claim, _hand, _state

PAGE = "# Results\n\n- [pandas 2.0 whatsnew](https://pandas.example/whatsnew#top)\n- plain text"


def _result(client, jid, **body):
    data = {
        "title": "Google 搜尋",
        "url": "https://www.google.com/search?q=pandas",
        "markdown": PAGE,
        "date": "2026-10-12",
        **body,
    }
    return client.post(f"/ai-workspace-helper/jobs/{jid}/result", json=data)


def _taken(client, iid):
    _beat(client)
    assert _hand(client, iid).status_code == 201
    return _claim(client).json()["id"]


def test_what_the_helper_fetched_is_saved_and_answers_the_card():
    from tests.api.test_outside_answers import _asked

    client, spec, iid = _asked()
    jid = _taken(client, iid)

    r = _result(client, jid)

    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert path.startswith("lookups/2026-10-12-") and path.endswith(".md")
    saved = _read(client, iid, path)
    assert "由小幫手抓取" in saved  # who fetched it
    assert "https://www.google.com/search?q=pandas" in saved  # where from
    assert "[pandas 2.0 whatsnew](https://pandas.example/whatsnew#top)" in saved
    # …and its link, numbered, under it — the helper's answer goes through the
    # same door a pasted one does (plan-outside-helper D24)
    assert saved.rstrip().endswith("- [1] <https://pandas.example/whatsnew> pandas 2.0 whatsnew")
    [answer] = _answers(spec)
    assert answer.content.startswith("我在外面查了:pandas 2.0 新功能")
    assert path in answer.content
    assert _state(client, iid)["state"] == "done"


# The same shape as a hand-pasted answer: the same function writes both.
def test_a_helper_answer_reads_like_a_pasted_one():
    from tests.api.test_outside_answers import _asked

    by_hand, hand_spec, hand_iid = _asked()
    assert _answer(by_hand, hand_iid, content=PAGE, date="2026-10-12").status_code == 200
    by_helper, helper_spec, helper_iid = _asked()
    _result(by_helper, _taken(by_helper, helper_iid))

    [pasted] = _answers(hand_spec)
    [fetched] = _answers(helper_spec)
    assert pasted.content == fetched.content


# The message says what was actually searched — the query as the person
# edited it before 「查」 — as a hand-pasted answer does (D8).
def test_the_answer_names_the_query_the_person_edited():
    from tests.api.test_outside_answers import _asked

    client, spec, iid = _asked()
    _beat(client)
    _hand(client, iid, query="pandas 3 roadmap")
    jid = _claim(client).json()["id"]

    _result(client, jid)

    [answer] = _answers(spec)
    assert answer.content.startswith("我在外面查了:pandas 3 roadmap")


def test_a_helper_cannot_answer_with_a_job_that_is_not_its_own():
    from tests.api.test_outside_answers import _asked

    holder = {"id": "u"}
    client, spec, iid = _asked(holder=holder)
    jid = _taken(client, iid)

    holder["id"] = "someone-else"
    assert _result(client, jid).status_code == 404
    holder["id"] = "u"
    assert _answers(spec) == []
    assert _state(client, iid)["state"] == "claimed"


def test_a_job_not_taken_cannot_be_answered():
    from tests.api.test_outside_answers import _asked

    client, spec, iid = _asked()
    _beat(client)
    jid = _hand(client, iid).json()["id"]

    assert _result(client, jid).status_code == 404
    assert _answers(spec) == []


# §3.5: done, the card says where the page was saved.
def test_a_done_job_names_where_it_was_saved():
    from tests.api.test_outside_answers import _asked

    client, _spec, iid = _asked()
    jid = _taken(client, iid)

    saved = _result(client, jid).json()["path"]

    job = _state(client, iid)
    assert job["state"] == "done"
    assert job["saved"] == saved and saved.startswith("lookups/")
    assert job["auto"] is False


# Someone answered the card by hand while the helper was fetching: theirs
# stands, the helper's is refused, and the job does not hang.
def test_a_card_answered_meanwhile_refuses_the_helpers_result():
    from tests.api.test_outside_answers import _asked

    client, spec, iid = _asked()
    jid = _taken(client, iid)
    assert _answer(client, iid, kind="not_found").status_code == 200

    r = _result(client, jid)

    assert r.status_code == 409
    assert len(_answers(spec)) == 1
    assert _state(client, iid)["state"] == "cancelled"


# A page that can never be sent (an empty page, a PDF viewer; a chat deleted;
# the right to send revoked) must end the job, with why — left claimed it went
# back to the queue every two minutes, to be fetched and refused forever
# (review round 1).
def test_a_result_that_cannot_be_sent_ends_the_job_with_why():
    from tests.api.test_outside_answers import _asked
    from workspace_app.api.outside_jobs import CLAIM_TIMEOUT_MS

    client, spec, iid = _asked()
    store = client.app.state.outside_jobs
    t = [1_000_000]
    store.now = lambda: t[0]
    jid = _taken(client, iid)

    r = _result(client, jid, markdown="   ")

    assert r.status_code == 422
    state = _state(client, iid)
    assert state["state"] == "failed" and "nothing to send" in state["error"]
    t[0] += CLAIM_TIMEOUT_MS + 1
    _beat(client)
    assert _claim(client).status_code == 204
    assert _answers(spec) == []


# A refusal that may pass (the server briefly unable — a 5xx) leaves the job
# claimed, to be taken and tried again; only a page that can never be sent
# ends it (review round 2).
def test_a_result_refused_for_now_is_tried_again():
    from tests.api.test_outside_answers import _asked
    from workspace_app.api.request_env import IRequestEnv

    down = [False]

    class _Source(IRequestEnv):
        async def env_for(self, request, *, user_id, item_id):
            if down[0]:
                raise RuntimeError("the env source is briefly down")
            return {}

    client, _spec, iid = _asked(request_env=_Source())
    jid = _taken(client, iid)
    down[0] = True

    r = _result(client, jid)

    assert r.status_code == 500
    assert _state(client, iid)["state"] == "claimed"


# A refusal's reason reaches the card as words, not a dict's repr.
def test_a_refusals_reason_is_said_in_words():
    from tests.api.test_outside_answers import _asked

    client, _spec, iid = _asked()
    jid = _taken(client, iid)

    _result(client, jid, markdown="  ")

    assert _state(client, iid)["error"] == "nothing to send — paste or attach"


# A result too large for the workspace's file limit is refused like a paste.
def test_a_result_larger_than_a_file_may_be_is_refused():
    from tests.api.conftest import register_rca_item
    from tests.api.test_outside_answers import _app

    client, spec = _app(max_file_size=1000)
    iid = register_rca_item(spec)
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"}).status_code == 202
    jid = _taken(client, iid)

    r = _result(client, jid, markdown="x" * 1001)

    assert r.status_code == 413
    assert _state(client, iid)["state"] == "failed"
