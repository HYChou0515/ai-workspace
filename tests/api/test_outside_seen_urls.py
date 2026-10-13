"""A URL an earlier lookup brought back opens with no nod (docs/plan-outside-helper.md D12).

Links in anything a lookup in this chat returned — fetched by the helper or
pasted by a person — are outside content, so opening one sends nothing the
person has not already seen come in. A URL the AI wrote itself could carry
internal words, so that one waits for 「查」. The server holds that line: the
card's automatic hand-over (`auto`) is refused for any other URL.
"""

from __future__ import annotations

import json

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from tests.api.test_outside_answers import _answer
from tests.api.test_outside_jobs import _beat, _claim
from workspace_app.agent.outside_lookup import OUTSIDE_LOOKUP_MARKER
from workspace_app.api import MessageDelta, RunDone, ToolEnd, ToolStart, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


class _Turns:
    """A different script each turn."""

    def __init__(self, *turns):
        self._turns = list(turns)

    async def run(self, prompt, ctx):  # noqa: ANN001, ANN201
        for ev in self._turns.pop(0):
            yield ev


def _card(call: str, **card) -> list:
    out = f"Asked.{OUTSIDE_LOOKUP_MARKER}{json.dumps({'why': 'need it', **card})}"
    return [
        ToolStart(call_id=call, name="ask_outside", args={"why": "need it", **card}),
        ToolEnd(call_id=call, output=out),
        MessageDelta(text="ok"),
        RunDone(),
    ]


def _chat(url: str = "", **second):
    """Turn 1 asks to search (`c1`); turn 2 asks to open `url` (`c2`) — or, given
    `second`, draws that card instead."""
    spec = make_spec(default_user="u")
    runner = _Turns(_card("c1", query="pandas"), _card("c2", **(second or {"url": url})))
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            get_user_id=lambda: "u",
        )
    )
    iid = register_rca_item(spec)
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q1"}).status_code == 202
    return client, iid


def _second_turn(client, iid):
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q2"}).status_code == 202


def _auto(client, iid, call="c2"):
    return client.post(
        f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": call, "auto": True}
    )


PAGE = "results\n- [whatsnew](https://pandas.example/whatsnew#top)\n- <https://b.example/x>"


def test_a_link_a_pasted_answer_brought_back_opens_without_a_nod():
    client, iid = _chat("https://pandas.example/whatsnew")
    _beat(client)
    assert _answer(client, iid, content=PAGE).status_code == 200
    _second_turn(client, iid)

    r = _auto(client, iid)

    assert r.status_code == 201, r.text
    job = _claim(client).json()
    assert (job["kind"], job["args"]) == ("open_url", "https://pandas.example/whatsnew")


def test_a_link_the_helper_brought_back_opens_without_a_nod():
    client, iid = _chat("https://b.example/x")
    _beat(client)
    client.post(f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": "c1"})
    jid = _claim(client).json()["id"]
    r = client.post(f"/ai-workspace/jobs/{jid}/result", json={"markdown": PAGE})
    assert r.status_code == 200, r.text
    _second_turn(client, iid)

    assert _auto(client, iid).status_code == 201


def test_a_url_no_lookup_brought_back_waits_for_the_nod():
    client, iid = _chat("https://internal-words.example/?q=secret")
    _beat(client)
    assert _answer(client, iid, content=PAGE).status_code == 200
    _second_turn(client, iid)

    r = _auto(client, iid)

    assert r.status_code == 409, r.text
    assert _claim(client).status_code == 204
    # …and the person's 「查」 is the nod
    r = client.post(f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": "c2"})
    assert r.status_code == 201


def test_a_search_card_never_goes_without_the_nod():
    client, iid = _chat("https://pandas.example/whatsnew")
    _beat(client)

    assert _auto(client, iid, call="c1").status_code == 409


# …even when what it would search for is, character for character, a link a
# lookup brought back: a search sends the AI's words out, and those wait.
def test_a_search_for_a_seen_link_still_waits_for_the_nod():
    client, iid = _chat(query="https://pandas.example/whatsnew")
    _beat(client)
    assert _answer(client, iid, content=PAGE).status_code == 200
    _second_turn(client, iid)

    assert _auto(client, iid).status_code == 409


# Opened automatically once; a second automatic request (another tab drawing
# the same card) must not start it over.
def test_an_automatic_request_leaves_a_job_already_running_alone():
    client, iid = _chat("https://pandas.example/whatsnew")
    _beat(client)
    _answer(client, iid, content=PAGE)
    _second_turn(client, iid)
    assert _auto(client, iid).status_code == 201
    assert _claim(client).status_code == 200

    r = _auto(client, iid)

    assert r.status_code == 201
    assert r.json()["state"] == "claimed"
    assert _claim(client).status_code == 204


# D12's no-nod open belongs to the person whose answer led to it (plan §3.2:
# "為送出這一輪的人"), not to whoever else in a shared item has the chat open —
# their helper would fetch it as them (review round 1).
def test_a_link_opens_by_itself_only_for_the_person_who_asked():
    spec = make_spec(default_user="u")
    holder = {"id": "u"}
    runner = _Turns(_card("c1", query="pandas"), _card("c2", url="https://pandas.example/whatsnew"))
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            get_user_id=lambda: holder["id"],
        )
    )
    iid = register_rca_item(spec)  # public: anyone may converse
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q1"}).status_code == 202
    assert _answer(client, iid, call="c1", content=PAGE).status_code == 200

    holder["id"] = "colleague"
    _beat(client)
    assert _auto(client, iid).status_code == 409

    holder["id"] = "u"
    _beat(client)
    assert _auto(client, iid).status_code == 201


# The person stopped a page that opened by itself: reloading the chat (the card
# asks again on every mount) must not start it again — the card offers 「查」
# instead (review round 2).
def test_a_stopped_automatic_open_is_not_started_again():
    client, iid = _chat("https://pandas.example/whatsnew")
    _beat(client)
    _answer(client, iid, content=PAGE)
    _second_turn(client, iid)
    assert _auto(client, iid).status_code == 201
    assert client.delete(f"/a/rca/items/{iid}/outside-jobs/c2").status_code == 204

    r = _auto(client, iid)

    assert r.json()["state"] == "cancelled"
    assert _claim(client).status_code == 204


class _Watching(_Turns):
    """Records, as each turn STARTS, whether `url` is already a seen link."""

    def __init__(self, spec, url, *turns):
        super().__init__(*turns)
        self._spec, self._url = spec, url
        self.seen_at_start: list[bool] = []

    async def run(self, prompt, ctx):  # noqa: ANN001, ANN201
        from workspace_app.api.outside_jobs import _OutsideSeenUrls

        rm = self._spec.get_resource_manager(_OutsideSeenUrls)
        rows = [rm.get(m.resource_id).data for m in rm.search_resources(query=None)]
        self.seen_at_start.append(any(self._url in row.urls for row in rows))
        async for ev in super().run(prompt, ctx):
            yield ev


# The answer's message starts the AI's next turn, and that turn may at once ask
# to open a link the answer brought back — the card then asks the server whether
# the link was seen. Remembered only after the message went out, the card could
# lose that race and ask for a nod D12 says it need not (found live in P10).
def test_a_link_is_already_seen_when_the_next_turn_starts():
    spec = make_spec(default_user="u")
    runner = _Watching(
        spec,
        "https://pandas.example/whatsnew",
        _card("c1", query="pandas"),
        _card("c2", url="https://pandas.example/whatsnew"),
    )
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            get_user_id=lambda: "u",
        )
    )
    iid = register_rca_item(spec)
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q1"}).status_code == 202

    assert _answer(client, iid, call="c1", content=PAGE).status_code == 200

    assert runner.seen_at_start == [False, True]
