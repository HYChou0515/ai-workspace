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
        {**t, "description": "", "preview": True} for t in targets
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
        },
        {
            "name": "Parts",
            "url": "partsdb://lookup?pn={q}",
            "description": "A full part number only.",
            "preview": False,
        },
    ]
