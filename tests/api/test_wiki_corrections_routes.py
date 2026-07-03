"""#397 — the wiki-correction HTTP surface: submit a correction (the FE "回報有誤"
path) and the AI drafting assist. The submit route converges with the
request_wiki_update agent tool on coordinator.submit_correction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from agents import RunContextWrapper
from httpx import ASGITransport

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import write_file_impl
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.store import CORRECTIONS_DIR
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient


class _CorrectorRunner:
    """Applies a correction by editing the named page (proves the job runs)."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        wrapped = RunContextWrapper(ctx)
        await write_file_impl(wrapped, "/entities/foo.md", "corrected\n")
        yield RunDone()


class _JsonLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._reply, False)


def _app(spec, *, answer_llm=None):
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_CorrectorRunner(),
        get_user_id=lambda: "alice",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
        answer_llm=answer_llm,
    )


async def _wiki_collection(c) -> str:
    return (await c.post("/kb/collections", json={"name": "c", "use_wiki": True})).json()[
        "resource_id"
    ]


async def test_submit_correction_records_and_queues():
    spec = make_spec(default_user="alice")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _wiki_collection(c)
        r = await c.post(
            f"/kb/collections/{cid}/wiki/corrections",
            json={
                "instruction": "Foo was founded in 1998, not 1989.",
                "target_page": "/entities/foo.md",
            },
        )
        assert r.status_code == 200
        path = r.json()["path"]
        assert path.startswith(CORRECTIONS_DIR)
        await app.state.wiki_coordinator.aclose()  # the corrector job runs

        # the correction is on the immune page
        page = (await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": path})).json()
        assert "1998" in page["content"]


async def test_submit_correction_rejects_a_non_wiki_collection():
    spec = make_spec(default_user="alice")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "plain"})).json()["resource_id"]
        r = await c.post(
            f"/kb/collections/{cid}/wiki/corrections", json={"instruction": "x is wrong"}
        )
        assert r.status_code == 400


async def test_submit_correction_rejects_an_empty_instruction():
    spec = make_spec(default_user="alice")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _wiki_collection(c)
        r = await c.post(f"/kb/collections/{cid}/wiki/corrections", json={"instruction": "   "})
        assert r.status_code == 422


async def test_draft_endpoint_returns_a_draft():
    spec = make_spec(default_user="alice")
    llm = _JsonLlm('{"action": "draft", "instruction": "Founded 1998.", "target_page": "/f.md"}')
    app = _app(spec, answer_llm=llm)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _wiki_collection(c)
        r = await c.post(
            f"/kb/collections/{cid}/wiki/corrections/draft",
            json={"question": "When founded?", "answer": "1989.", "wiki_pages": ["/f.md"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["action"] == "draft"
        assert body["instruction"] == "Founded 1998."
        assert body["target_page"] == "/f.md"


async def test_draft_endpoint_can_ask_clarifying_questions():
    spec = make_spec(default_user="alice")
    llm = _JsonLlm('{"action": "ask", "questions": ["Which fact is wrong?"]}')
    app = _app(spec, answer_llm=llm)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _wiki_collection(c)
        r = await c.post(
            f"/kb/collections/{cid}/wiki/corrections/draft",
            json={"question": "q", "answer": "a"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["action"] == "ask"
        assert body["questions"] == ["Which fact is wrong?"]
