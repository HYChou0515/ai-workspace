"""Model-sanity battery HTTP surface: GET /sanity/questions (matrix metadata),
POST /sanity/run (cell / battery), and the auto GET /sanity-result the FE lists.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.resources import SanityResult, make_spec, sanity_result_id
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

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


def _app_and_spec(*, judge: ILlm | None = None):
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        sanity_llm_factory=_sanity_llm_factory,
        sanity_models=[_MODEL],
        sanity_judge_llm=judge,
    )
    return app, spec


class _JudgeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield '{"grade": "pass", "note": "符合期望"}', False


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


def test_results_row_exposes_ai_grade_and_note():
    """#231 P1: each cell row carries the AI judge's verdict + note alongside the
    mechanical grade. No judge is wired here, so both are empty — but the contract
    fields exist (the FE table renders an ai評分/ai評語 column)."""
    app, spec = _app_and_spec()
    with TestClient(app) as client:
        meta = client.get("/sanity/questions").json()
        taipei = next(q for q in meta["questions"] if q["category"] == "基礎知識")
        client.post(
            "/sanity/run",
            json={"model": _MODEL, "scope": "cell", "question_key": taipei["key"], "level": "none"},
        )
        rm = spec.get_resource_manager(SanityResult)
        _poll_exists(rm, sanity_result_id(_MODEL, taipei["key"], "none"))
        row = next(
            r
            for r in client.get("/sanity/results", params={"model": _MODEL}).json()
            if r["question_key"] == taipei["key"]
        )
        assert row["ai_grade"] == "" and row["ai_note"] == ""


def test_judge_wired_through_create_app_fills_ai_columns():
    """#231 P2: a judge wired via create_app grades each cell end-to-end — the
    FE-shaped row carries the AI verdict + note."""
    app, spec = _app_and_spec(judge=_JudgeLlm())
    with TestClient(app) as client:
        meta = client.get("/sanity/questions").json()
        taipei = next(q for q in meta["questions"] if q["category"] == "基礎知識")
        client.post(
            "/sanity/run",
            json={"model": _MODEL, "scope": "cell", "question_key": taipei["key"], "level": "none"},
        )
        rm = spec.get_resource_manager(SanityResult)
        _poll_exists(rm, sanity_result_id(_MODEL, taipei["key"], "none"))
        row = next(
            r
            for r in client.get("/sanity/results", params={"model": _MODEL}).json()
            if r["question_key"] == taipei["key"]
        )
        assert row["ai_grade"] == "pass" and row["ai_note"] == "符合期望"


def test_get_verdicts_lists_per_model_cards():
    """#231 P3: GET /sanity/verdicts returns one fitness card per judged model."""
    from workspace_app.resources import SanityVerdict, sanity_verdict_id

    app, spec = _app_and_spec(judge=_JudgeLlm())
    rm = spec.get_resource_manager(SanityVerdict)
    rm.create(
        SanityVerdict(model=_MODEL, score=77, summary="- KB 問答 OK\n- JSON 強"),
        resource_id=sanity_verdict_id(_MODEL),
    )
    with TestClient(app) as client:
        cards = client.get("/sanity/verdicts").json()
        assert any(
            c["model"] == _MODEL and c["score"] == 77 and "JSON" in c["summary"] for c in cards
        )


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


def test_run_missing_endpoint_fills_blanks_for_models():
    """#231 P4: POST /sanity/run-missing enqueues the never-run coverage cells for
    the selected models and reports how many it queued."""
    from specstar import QB

    app, spec = _app_and_spec()
    with TestClient(app) as client:
        resp = client.post("/sanity/run-missing", json={"models": [_MODEL]})
        assert resp.status_code == 202 and resp.json()["count"] > 5

        rm = spec.get_resource_manager(SanityResult)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if rm.count_resources((QB["model"] == _MODEL).build()) > 5:
                break
            time.sleep(0.05)
        assert rm.count_resources((QB["model"] == _MODEL).build()) > 5


def test_rescore_endpoint_rejudges_and_returns_count():
    """#231 P4: POST /sanity/rescore re-judges stored cell outputs and reports the
    count (here: the one cell that was run)."""
    app, spec = _app_and_spec(judge=_JudgeLlm())
    with TestClient(app) as client:
        meta = client.get("/sanity/questions").json()
        taipei = next(q for q in meta["questions"] if q["category"] == "基礎知識")
        client.post(
            "/sanity/run",
            json={"model": _MODEL, "scope": "cell", "question_key": taipei["key"], "level": "none"},
        )
        rm = spec.get_resource_manager(SanityResult)
        _poll_exists(rm, sanity_result_id(_MODEL, taipei["key"], "none"))

        resp = client.post("/sanity/rescore", json={"models": [_MODEL]})
        assert resp.status_code == 200 and resp.json()["count"] == 1


def test_custom_question_authored_via_crud_appears_in_meta_and_runs():
    """#231 P5: a user-authored question (specstar auto-CRUD) shows up in the
    matrix metadata and runs AI-only (no mechanical grader)."""
    app, spec = _app_and_spec(judge=_JudgeLlm())
    with TestClient(app) as client:
        created = client.post(
            "/custom-sanity-question",
            json={
                "category": "自訂",
                "prompt": "台灣最高的山?",
                "expected": "玉山",
                "levels": ["none"],
            },
        )
        assert created.status_code in (200, 201)

        meta = client.get("/sanity/questions").json()
        custom = next((q for q in meta["questions"] if q["category"] == "自訂"), None)
        assert custom is not None and custom["expected"] == "玉山"

        client.post(
            "/sanity/run",
            json={"model": _MODEL, "scope": "cell", "question_key": custom["key"], "level": "none"},
        )
        rm = spec.get_resource_manager(SanityResult)
        cell = _poll_exists(rm, sanity_result_id(_MODEL, custom["key"], "none"))
        assert cell.grade == "" and cell.ai_grade == "pass"  # no mechanical grader; judge ran


def test_custom_question_crud_lifecycle():
    """#231 P8 backend: typed /sanity/custom-questions CRUD — create → list →
    update → delete, with 404 on an unknown id."""
    app, _ = _app_and_spec()
    with TestClient(app) as client:
        body = {"category": "自訂", "prompt": "p", "expected": "e", "levels": ["none"]}
        created = client.post("/sanity/custom-questions", json=body)
        assert created.status_code == 201
        qid = created.json()["id"]

        listed = client.get("/sanity/custom-questions").json()
        assert any(q["id"] == qid and q["prompt"] == "p" for q in listed)

        upd = client.put(
            f"/sanity/custom-questions/{qid}",
            json={**body, "prompt": "p2", "enabled": False},
        )
        assert upd.status_code == 200 and upd.json()["prompt"] == "p2"
        after = client.get("/sanity/custom-questions").json()
        assert any(q["id"] == qid and q["prompt"] == "p2" and q["enabled"] is False for q in after)

        assert client.delete(f"/sanity/custom-questions/{qid}").status_code == 204
        assert client.get("/sanity/custom-questions").json() == []

        # unknown id ⇒ 404 on both update and delete
        assert client.put("/sanity/custom-questions/nope", json=body).status_code == 404
        assert client.delete("/sanity/custom-questions/nope").status_code == 404


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
