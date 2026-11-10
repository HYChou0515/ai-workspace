"""The person's chosen search target reaches their helper
(docs/plan-lookup-target-choice.md §3.4, P4).

The person picks the target on the card (T1); the backend fills that target's
`url` with what they typed and hands the helper THAT string — the helper is a
black box that interprets it (T6), and no longer picks a target of its own.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.api.test_outside_answers import _asked, _read
from tests.api.test_outside_job_results import _result
from tests.api.test_outside_jobs import _beat, _claim, _hand

TARGETS = [
    {"name": "Google", "url": "https://www.google.com/search?q={q}"},
    {
        "name": "Parts",
        "url": "partsdb://lookup?pn={q}&exact=1",
        "description": "Full part numbers only.",
        "preview": False,
    },
]


def _asked_with_targets():
    return _asked(lookup_targets=TARGETS)


def test_the_helper_gets_the_target_the_person_chose_filled_with_what_they_typed():
    client, _spec, iid = _asked_with_targets()
    _beat(client)

    r = _hand(client, iid, query="A123 4567", target="Parts")

    assert r.status_code == 201, r.text
    job = _claim(client).json()
    assert job["kind"] == "search"
    assert job["target"] == "Parts"
    assert job["target_url"] == "partsdb://lookup?pn=A123%204567&exact=1"
    # what was searched stays what the person typed — it heads the saved page
    assert job["args"] == "A123 4567"


def test_a_card_from_before_this_deploy_searches_the_first_target():
    """A tab left open across the deploy sends no target: it gets what the
    picker would have shown chosen — the first target (T2)."""
    client, _spec, iid = _asked_with_targets()
    _beat(client)

    assert _hand(client, iid, query="q").status_code == 201

    job = _claim(client).json()
    assert (job["target"], job["target_url"]) == ("Google", "https://www.google.com/search?q=q")


def test_a_target_the_deploy_does_not_list_is_refused_and_nothing_is_queued():
    client, _spec, iid = _asked_with_targets()
    _beat(client)

    r = _hand(client, iid, query="q", target="Bing")

    assert r.status_code == 422, r.text
    assert "Bing" in r.json()["detail"]
    assert _claim(client).status_code == 204


def test_the_saved_page_says_which_target_it_came_from():
    """T10: the target the person used, as a hand-pasted answer records it."""
    client, _spec, iid = _asked_with_targets()
    _beat(client)
    assert _hand(client, iid, query="A123", target="Parts").status_code == 201
    jid = _claim(client).json()["id"]

    r = _result(client, jid, url="partsdb://lookup?pn=A123&exact=1")

    assert r.status_code == 200, r.text
    assert "- 用:Parts" in _read(client, iid, r.json()["path"])


def test_a_helper_on_the_old_protocol_is_told_to_update():
    """§3.4: the job's meaning moved (a filled target, not keywords to put into
    a target of its own choosing), so a protocol-1 helper — which would search
    Google for the whole string — is refused with 426 rather than misread it."""
    client, _spec, _iid = _asked_with_targets()
    as_helper = {"X-AI-Workspace-Helper-Version": "0.1.0", "X-AI-Workspace-Helper-Protocol": "1"}

    r = client.post("/ai-workspace-helper/jobs/claim", headers=as_helper)

    assert r.status_code == 426, r.text


def test_the_fill_is_the_cards_own():
    """The card opens `searchUrl(target, query)` by hand; the helper gets the
    backend's fill. One target, one address: both are held to the same table
    (`tests/fixtures/search_fill_cases.json`; the card's half is
    `web/tests/searchFillParity.test.ts`)."""
    from workspace_app.api.outside_jobs import fill_target

    table = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "search_fill_cases.json").read_text()
    )
    assert len(table["cases"]) >= 5
    for case in table["cases"]:
        assert fill_target(case["url"], case["query"]) == case["filled"], case
