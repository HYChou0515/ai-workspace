"""Context-card generation routes (#175) — the HTTP surface over the
``CardGenCoordinator``. A scripted runner drives the agentic ``AgentCardDrafter``
(#506) through ``create_app`` so the route → job → draft → review → commit
round-trip is proven end-to-end; the job is drained synchronously between calls.
``card_drafter_llm`` stays non-None purely to ENABLE drafting — the agentic drafter
digests via the runner's final message, not that ILlm."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

from specstar import QB
from specstar.types import Binary

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.llm import ILlm
from workspace_app.resources import Collection, ContextCard, SourceDoc, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient as ApiTestClient


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


_ONE_CARD = json.dumps(
    {
        "cards": [
            {
                "title": "Reflow Zone 3",
                "keys": ["RZ3", "Reflow Zone 3"],
                "body": "The third reflow zone.",
                "confident": True,
                "snippet": "RZ3 is the third reflow zone.",
            }
        ]
    }
)


def _make_app(canned_json: str):
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        # #506: the agentic drafter reads the digest from the runner's final message.
        runner=ScriptedAgentRunner([MessageDelta(text=canned_json), RunDone()]),
        card_drafter_llm=_FakeLlm(canned_json),  # non-None ⇒ drafting enabled
    )
    return spec, app


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _add_source(spec, cid: str, path: str, text: str) -> str:
    return (
        spec.get_resource_manager(SourceDoc)
        .create(
            SourceDoc(
                collection_id=cid,
                path=path,
                content=Binary(data=text.encode()),
                text=text,
                status="ready",
            ),
            resource_id=encode_doc_id(cid, path),
        )
        .resource_id
    )


def _list_cards(spec, cid: str) -> list[ContextCard]:
    rm = spec.get_resource_manager(ContextCard)
    return [r.data for r in rm.list_resources((QB["collection_id"] == cid).build())]


def test_generate_review_commit_roundtrip():
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)

    r = client.post(f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    asyncio.run(app.state.card_gen_coordinator.aclose())  # drain the queued run

    r = client.get(f"/kb/context-card-gen/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    (prop,) = body["proposals"]
    assert prop["mode"] == "new"
    assert prop["keys"] == ["RZ3", "Reflow Zone 3"]
    assert prop["provenance"][0]["path"] == "a.md"
    assert prop["provenance"][0]["snippet"] == "RZ3 is the third reflow zone."

    prop["decision"] = "accepted"
    r = client.post(f"/kb/context-card-gen/{job_id}/review", json={"proposals": [prop]})
    assert r.status_code == 200
    assert r.json()["proposals"][0]["decision"] == "accepted"  # persisted (resumable)

    r = client.post(f"/kb/context-card-gen/{job_id}/commit")
    assert r.status_code == 200
    assert r.json() == {"created": 1, "updated": 0, "skipped": 0}

    (card,) = _list_cards(spec, cid)
    assert card.keys == ["RZ3", "Reflow Zone 3"]
    assert card.norm_keys == ["reflow zone 3", "rz3"]
    assert card.body == "The third reflow zone."


def test_status_exposes_the_finalize_funnel_counts():
    """#506/#577 follow-up: the status route carries the finalize funnel (units
    digested, raw drafts extracted, proposals kept) so the FE can show
    'drafted X → kept Y' — the signal that lets a user see the drafter is the
    bottleneck (few drafts) vs reconcile (many drafts, few proposals)."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)

    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    body = client.get(f"/kb/context-card-gen/{job_id}").json()
    assert body["n_units"] == 1
    assert body["n_raw_drafts"] == 1
    assert body["n_proposals"] == 1


def test_latest_run_funnel_route_reports_the_collections_last_run():
    """The 待審核 tab reads the collection's last finalized run's funnel to show
    'drafted X → kept Y'. Before any run it's null; after a run it carries the
    counts — including the kept=0 case the active-proposal queue can't show."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    client = ApiTestClient(app)

    assert client.get(f"/kb/collections/{cid}/context-card-gen/latest").json() is None

    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client.post(f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]})
    asyncio.run(app.state.card_gen_coordinator.aclose())

    body = client.get(f"/kb/collections/{cid}/context-card-gen/latest").json()
    assert body["n_units"] == 1
    assert body["n_raw_drafts"] == 1
    assert body["n_proposals"] == 1


def test_generate_does_not_change_auto_digest():
    """Generate is a one-shot action over the picked docs — it must NOT silently
    flip the collection's ``auto_digest``. That flag is a user-owned setting
    (toggled from the collection settings panel); only the user turns it on, so
    a single generate can't opt the collection into perpetual auto-generation +
    proactive questions behind their back."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    rm = spec.get_resource_manager(Collection)
    before = rm.get(cid).data
    assert isinstance(before, Collection) and before.auto_digest is False  # default off

    client = ApiTestClient(app)
    r = client.post(f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]})
    assert r.status_code == 200

    after = rm.get(cid).data
    assert isinstance(after, Collection) and after.auto_digest is False  # untouched by generate


def test_pending_queue_lists_a_finalized_run_and_commit_removes_it():
    """#415: a finalized run is a row in the collection's 待審核 queue; committing
    it (via the existing commit route) resolves it out of the queue."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)

    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    pending = client.get(f"/kb/collections/{cid}/context-card-gen")
    assert pending.status_code == 200
    assert pending.json() == [{"run_id": job_id, "collection_id": cid, "proposal_count": 1}]

    prop = client.get(f"/kb/context-card-gen/{job_id}").json()["proposals"][0]
    prop["decision"] = "accepted"
    client.post(f"/kb/context-card-gen/{job_id}/review", json={"proposals": [prop]})
    client.post(f"/kb/context-card-gen/{job_id}/commit")

    assert client.get(f"/kb/collections/{cid}/context-card-gen").json() == []


def test_dismiss_route_removes_a_run_from_the_queue():
    """#415: dismissing a run drops it from the queue and writes no card."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)

    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    assert client.get(f"/kb/collections/{cid}/context-card-gen").json()
    assert client.post(f"/kb/context-card-gen/{job_id}/dismiss").status_code == 200
    assert client.get(f"/kb/collections/{cid}/context-card-gen").json() == []
    assert _list_cards(spec, cid) == []


def test_decide_route_sets_one_cards_decision():
    """#481: the inline accept/reject persists one card's decision by id."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)
    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    card_id = client.get(f"/kb/context-card-gen/{job_id}").json()["proposals"][0]["id"]
    r = client.post(
        f"/kb/context-card-gen/{job_id}/decide", json={"card_id": card_id, "decision": "accepted"}
    )
    assert r.status_code == 200
    got = client.get(f"/kb/context-card-gen/{job_id}").json()["proposals"][0]
    assert got["id"] == card_id
    assert got["decision"] == "accepted"


def test_update_proposal_route_persists_a_drawer_edit():
    """#481: the drawer edit persists an edited card (body + decision) by id."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)
    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    prop = client.get(f"/kb/context-card-gen/{job_id}").json()["proposals"][0]
    prop["body"] = "edited body"
    prop["decision"] = "accepted"
    r = client.post(f"/kb/context-card-gen/{job_id}/proposals/{prop['id']}", json=prop)
    assert r.status_code == 200
    got = client.get(f"/kb/context-card-gen/{job_id}").json()["proposals"][0]
    assert got["body"] == "edited body"
    assert got["decision"] == "accepted"


def test_commit_cards_route_writes_referenced_cards():
    """#481: the multi-card commit takes ``cards: [{run_id, card_id}]`` and writes
    exactly those, returning the aggregated tallies."""
    spec, app = _make_app(_ONE_CARD)
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone.")
    client = ApiTestClient(app)
    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    card_id = client.get(f"/kb/context-card-gen/{job_id}").json()["proposals"][0]["id"]
    client.post(
        f"/kb/context-card-gen/{job_id}/decide", json={"card_id": card_id, "decision": "accepted"}
    )
    r = client.post(
        "/kb/context-card-gen/commit", json={"cards": [{"run_id": job_id, "card_id": card_id}]}
    )
    assert r.status_code == 200
    assert r.json() == {"created": 1, "updated": 0, "skipped": 0}
    assert len(_list_cards(spec, cid)) == 1
    assert client.get(f"/kb/collections/{cid}/context-card-gen").json() == []  # run settled


def test_generate_with_no_drafter_llm_completes_with_no_proposals():
    """With no card-drafting LLM wired the feature stays mounted but proposes
    nothing — the run still completes (the FE shows '沒有新卡片可建議')."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "anything")
    client = ApiTestClient(app)

    job_id = client.post(
        f"/kb/collections/{cid}/context-cards/generate", json={"doc_ids": [doc]}
    ).json()["job_id"]
    asyncio.run(app.state.card_gen_coordinator.aclose())

    body = client.get(f"/kb/context-card-gen/{job_id}").json()
    assert body["status"] == "completed"
    assert body["proposals"] == []
