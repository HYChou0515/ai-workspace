"""Context-card generation routes (#175) — the HTTP surface over the
``CardGenCoordinator``. A fake ``ILlm`` drives a real ``LlmCardDrafter`` through
``create_app`` so the route → job → draft → review → commit round-trip is proven
end-to-end; the job is drained synchronously between calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

from specstar import QB
from specstar.types import Binary

from workspace_app.api import ScriptedAgentRunner, create_app
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
        runner=ScriptedAgentRunner([]),
        card_drafter_llm=_FakeLlm(canned_json),
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
