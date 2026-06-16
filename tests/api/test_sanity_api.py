"""Model-sanity battery HTTP surface: GET /sanity/questions (matrix metadata),
POST /sanity/run (cell / battery), and the auto GET /sanity-result the FE lists.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator

from fastapi.testclient import TestClient

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.resources import SanityResult, make_spec, sanity_result_id
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

_MODEL = "ollama_chat/qwen3:14b"


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


class _FakeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield "首都是台北市", False  # no reasoning


def _sanity_llm_factory(model: str, level: str) -> ILlm:
    return _FakeLlm()


def _app_and_spec():
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        sanity_llm_factory=_sanity_llm_factory,
        sanity_models=[_MODEL],
    )
    return app, spec


def _poll_exists(rm, rid: str, timeout: float = 5.0) -> SanityResult:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rm.exists(rid):
            return rm.get(rid).data
        time.sleep(0.02)
    raise AssertionError(f"cell {rid} never landed")


def test_get_questions_returns_models_levels_and_19_questions():
    app, _ = _app_and_spec()
    body = TestClient(app).get("/sanity/questions").json()
    assert body["models"] == [_MODEL]
    assert [lvl["level"] for lvl in body["levels"]] == ["none", "low", "medium", "high"]
    assert [lvl["label"] for lvl in body["levels"]] == ["Off", "Low", "Medium", "High"]
    assert len(body["questions"]) == 19
    q = body["questions"][0]
    assert q["key"] and q["messages"] and q["expected"]
    assert isinstance(q["auto_levels"], list)


def test_post_run_cell_executes_grades_and_persists():
    app, spec = _app_and_spec()
    with TestClient(app) as client:
        meta = client.get("/sanity/questions").json()
        taipei = next(q for q in meta["questions"] if q["category"] == "基礎知識")
        resp = client.post(
            "/sanity/run",
            json={"model": _MODEL, "scope": "cell", "question_key": taipei["key"], "level": "none"},
        )
        assert resp.status_code == 202 and resp.json()["queued"] is True

        rm = spec.get_resource_manager(SanityResult)
        cell = _poll_exists(rm, sanity_result_id(_MODEL, taipei["key"], "none"))
        assert cell.output == "首都是台北市"
        assert cell.grade == "pass"  # mechanical grader ran (contains 台北)
        assert cell.reasoned is False

        # …and the FE-shaped list route the matrix hydrates from
        rows = client.get("/sanity/results", params={"model": _MODEL}).json()
        cellrow = next(
            r for r in rows if r["question_key"] == taipei["key"] and r["level"] == "none"
        )
        assert cellrow["output"] == "首都是台北市" and cellrow["grade"] == "pass"
        # a different model has no cells yet
        assert client.get("/sanity/results", params={"model": "other"}).json() == []


def test_post_run_battery_fills_multiple_cells():
    app, spec = _app_and_spec()
    with TestClient(app) as client:
        resp = client.post("/sanity/run", json={"model": _MODEL, "scope": "battery"})
        assert resp.status_code == 202

        rm = spec.get_resource_manager(SanityResult)
        from specstar import QB

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            n = rm.count_resources((QB["model"] == _MODEL).build())
            if n > 5:
                break
            time.sleep(0.05)
        assert rm.count_resources((QB["model"] == _MODEL).build()) > 5


def test_post_run_cell_validates_question_and_level():
    app, _ = _app_and_spec()
    client = TestClient(app)
    bad_q = client.post(
        "/sanity/run",
        json={"model": _MODEL, "scope": "cell", "question_key": "nope", "level": "none"},
    )
    assert bad_q.status_code == 404
    meta = client.get("/sanity/questions").json()
    key = meta["questions"][0]["key"]
    bad_lvl = client.post(
        "/sanity/run",
        json={"model": _MODEL, "scope": "cell", "question_key": key, "level": "ultra"},
    )
    assert bad_lvl.status_code == 422
