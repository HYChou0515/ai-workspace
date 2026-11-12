"""`GET /lookup-targets` — the buttons the "請幫我查" card draws
(docs/plan-outside-lookup.md D3): the deploy's list, as configured, so the
card never hard-codes a search engine."""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient as ApiTestClient
from .conftest import Harness


def test_unconfigured_is_google_alone(harness: Harness):
    assert harness.client.get("/lookup-targets").json() == [
        {
            "name": "Google",
            "url": "https://www.google.com/search?q={q}",
            "description": "",
            "preview": True,
            "max_words": 10,
            "max_cjk": 40,
        }
    ]


def test_the_configured_list_is_what_the_card_gets():
    """Passed through from `create_app` — the hop the composition root feeds."""
    spec = make_spec()
    targets = [
        {"name": "Bing", "url": "https://www.bing.com/search?q={q}"},
        {"name": "Wiki", "url": "http://wiki.example/?s={q}"},
    ]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        lookup_targets=targets,
    )

    assert ApiTestClient(app).get("/lookup-targets").json() == [
        {**t, "description": "", "preview": True, "max_words": 10, "max_cjk": 40} for t in targets
    ]


def test_every_target_reaches_the_card_whole():
    """docs/plan-lookup-target-choice.md T3/T5: what a deploy left out is filled
    in once, where the app takes the list — no description, and the card's own
    new-tab button drawn — so no reader of the list guesses a default."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        lookup_targets=[
            {"name": "Google", "url": "https://www.google.com/search?q={q}"},
            {
                "name": "Parts",
                "url": "partsdb://lookup?pn={q}",
                "description": "A full part number only.",
                "preview": False,
            },
        ],
    )

    assert ApiTestClient(app).get("/lookup-targets").json() == [
        {
            "name": "Google",
            "url": "https://www.google.com/search?q={q}",
            "description": "",
            "preview": True,
            "max_words": 10,
            "max_cjk": 40,
        },
        {
            "name": "Parts",
            "url": "partsdb://lookup?pn={q}",
            "description": "A full part number only.",
            "preview": False,
            "max_words": 10,
            "max_cjk": 40,
        },
    ]


def test_a_targets_own_length_limits_reach_the_card_list():
    """docs/plan-remove-ext-ai.md R3: unset is today's 10 words / 40 CJK
    characters; a deploy's own numbers, 0 included, pass through as written."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        lookup_targets=[
            {"name": "AI", "url": "https://ai.example/?prompt={q}", "max_words": 0, "max_cjk": 0},
            {"name": "Parts", "url": "partsdb://lookup?pn={q}", "max_words": 1},
        ],
    )

    got = ApiTestClient(app).get("/lookup-targets").json()
    assert [(t["name"], t["max_words"], t["max_cjk"]) for t in got] == [
        ("AI", 0, 0),
        ("Parts", 1, 40),
    ]
