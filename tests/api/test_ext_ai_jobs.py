"""The 問外部AI card's server side (docs/plan-ext-ai.md P1).

The card is the 請幫我查 card's third kind, `{why, question}`: handed to the
person's helper as a job of kind `ask_ext_ai` carrying the question as the
person left it in the box (D2), only to a helper that says it can (D9); the
answer — the helper's or a pasted one — is saved under `lookups/` and sent as
the external AI's account, not an original source (D10).
"""

from __future__ import annotations

import pytest

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from tests.api.test_outside_answers import _answer, _answers, _read
from workspace_app.agent.outside_lookup import OUTSIDE_LOOKUP_MARKER
from workspace_app.api import (
    AgentEvent,
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    ToolEnd,
    ToolStart,
    create_app,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

QUESTION = "Which is safer for a 2 GB CSV, pandas or polars?\nWe read it once a day."
_CARD = (
    f'Asked.{OUTSIDE_LOOKUP_MARKER}{{"why": "compare the two", '
    f'"question": "Which is safer for a 2 GB CSV, pandas or polars?\\nWe read it once a day."}}'
)
ANSWER = "Polars, for memory. See [the guide](https://pola.example/guide)."


def _asked():
    """An item whose chat holds a 問外部AI card (call `c1`)."""
    events: list[AgentEvent] = [
        ToolStart(call_id="c1", name="ask_ext_ai", args={"why": "compare the two"}),
        ToolEnd(call_id="c1", output=_CARD),
        MessageDelta(text="ok"),
        RunDone(),
    ]
    spec = make_spec(default_user=lambda: "u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner(events),
        get_user_id=lambda: "u",
    )
    client = TestClient(app)
    iid = register_rca_item(spec)
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"}).status_code == 202
    return client, spec, iid


def _beat(client, ext_ai: str = "company-ai") -> None:
    body = {"version": "0.1.0", "protocol": 1, "ext_ai": ext_ai}
    assert client.post("/ai-workspace-helper/heartbeat", json=body).status_code == 204


def _hand(client, iid, **body):
    body.setdefault("query", QUESTION)
    return client.post(f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": "c1", **body})


def _claim(client):
    return client.post("/ai-workspace-helper/jobs/claim")


# ── handing it over (D2, D9) ────────────────────────────────────────────────


def test_the_card_hands_the_question_as_the_person_left_it():
    client, _spec, iid = _asked()
    _beat(client)

    r = _hand(client, iid, query="Which is safer, pandas or polars? (edited)")

    assert r.status_code == 201, r.text
    job = _claim(client).json()
    assert job["kind"] == "ask_ext_ai"
    assert job["args"] == "Which is safer, pandas or polars? (edited)"


@pytest.mark.parametrize("query", ["", "   "], ids=["empty", "blank"])
def test_an_empty_box_asks_nothing(query):
    client, _spec, iid = _asked()
    _beat(client)

    r = _hand(client, iid, query=query)

    assert r.status_code == 422, r.text
    assert "問題" in r.json()["detail"]
    assert _claim(client).status_code == 204


def test_a_helper_with_no_external_ai_set_up_is_not_handed_it():
    """D9: an older helper, or one packed without a site file, cannot ask —
    the card stays on the manual flow rather than queue a job it fails."""
    client, _spec, iid = _asked()
    _beat(client, ext_ai="")

    r = _hand(client, iid)

    assert r.status_code == 409, r.text
    assert "外部AI" in r.json()["detail"]
    assert _claim(client).status_code == 204


def test_the_status_says_whether_the_helper_can_ask_the_external_ai():
    client, _spec, _iid = _asked()
    assert client.get("/ai-workspace-helper/status").json() == {"online": False, "ext_ai": False}

    _beat(client, ext_ai="")
    assert client.get("/ai-workspace-helper/status").json() == {"online": True, "ext_ai": False}

    _beat(client)
    assert client.get("/ai-workspace-helper/status").json() == {"online": True, "ext_ai": True}


def test_it_never_asks_by_itself():
    """D2: every question waits for the person's 「問外部AI」."""
    client, _spec, iid = _asked()
    _beat(client)

    assert _hand(client, iid, auto=True).status_code == 409
    assert _claim(client).status_code == 204


# ── the answer (D5, D10) ────────────────────────────────────────────────────


def _taken(client, iid):
    _beat(client)
    assert _hand(client, iid).status_code == 201
    return _claim(client).json()["id"]


def test_the_helpers_answer_is_saved_and_sent_as_the_external_ais_account():
    client, spec, iid = _asked()
    jid = _taken(client, iid)

    r = client.post(
        f"/ai-workspace-helper/jobs/{jid}/result",
        json={"title": "外部AI", "url": "", "markdown": ANSWER, "date": "2026-10-15"},
    )

    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert path.startswith("lookups/2026-10-15-") and path.endswith(".md")
    saved = _read(client, iid, path)
    assert saved.startswith("# 問外部AI:Which is safer for a 2 GB CSV")
    assert "compare the two" in saved  # why
    assert QUESTION in saved  # the whole question, as asked
    assert ANSWER in saved
    assert "由小幫手問" in saved
    assert saved.rstrip().endswith("- [1] <https://pola.example/guide> the guide")
    [msg] = _answers(spec)
    assert msg.content.startswith("我問了外部AI:Which is safer for a 2 GB CSV")
    assert "不是原始出處" in msg.content
    assert path in msg.content and ANSWER in msg.content


def test_a_pasted_answer_reads_the_same():
    client, spec, iid = _asked()

    r = _answer(client, iid, content=ANSWER, query=QUESTION)

    assert r.status_code == 200, r.text
    saved = _read(client, iid, r.json()["path"])
    assert saved.startswith("# 問外部AI:Which is safer for a 2 GB CSV")
    assert ANSWER in saved and "由小幫手問" not in saved
    [msg] = _answers(spec)
    assert msg.content.startswith("我問了外部AI:") and "不是原始出處" in msg.content


# Review round 1: the AI is pointed at "the links below" only when there are
# some — an answer with none, or a person who may not add files (no file, no
# list), leaves it looking for a list that is not there.
def test_an_answer_with_no_links_points_at_none():
    client, spec, iid = _asked()

    assert _answer(client, iid, content="Polars.", query=QUESTION).status_code == 200

    [msg] = _answers(spec)
    assert "不是原始出處" in msg.content
    assert "ask_outside" not in msg.content


def test_not_asking_says_so():
    client, spec, iid = _asked()

    r = _answer(client, iid, kind="not_found", reason="公司 AI 今天關了")

    assert r.status_code == 200, r.text
    [msg] = _answers(spec)
    assert msg.content.startswith("沒有問外部AI／不問了:Which is safer for a 2 GB CSV")
    assert "公司 AI 今天關了" in msg.content
