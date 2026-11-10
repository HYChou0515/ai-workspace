"""The LLM budget a person still has (docs/plan-llm-budget.md).

The platform records no spending: a deploy brings the numbers in through its own
`ILlmBudget` (`server.llm_budget`), and the platform only shows what it answers.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fastapi import Request

from workspace_app.api.llm_budget import BudgetLink, BudgetStatus, ILlmBudget
from workspace_app.config.schema import Settings
from workspace_app.factories import get_llm_budget


class StubBudget(ILlmBudget):
    """Module-level so the dotted-path resolver can import it."""

    async def budget_for(self, request: Request, *, user_id: str) -> BudgetStatus | None:
        return BudgetStatus(remaining=11, total=20, link=BudgetLink(label="apply", url="https://x"))


# ─── P1: the seam ───────────────────────────────────────────────────────────


def test_unconfigured_deploy_has_no_budget_source():
    """No ``server.llm_budget`` ⇒ the feature does not exist (D2)."""
    assert Settings().server.llm_budget == ""
    assert get_llm_budget(Settings().server.llm_budget) is None


def test_configured_dotted_path_is_resolved_to_an_instance():
    assert isinstance(get_llm_budget("tests.api.test_llm_budget.StubBudget"), StubBudget)


def test_a_class_that_is_not_a_budget_source_is_refused_at_startup():
    """Loud at boot, naming the key — not a budget row that silently never shows."""
    with pytest.raises(TypeError, match="server.llm_budget.*not an ILlmBudget subclass"):
        get_llm_budget("workspace_app.config.schema.Settings")


def test_every_field_of_the_answer_is_optional():
    """D4: a message alone, numbers alone, or nothing at all are all answers."""
    bare = BudgetStatus()
    assert (bare.remaining, bare.total, bare.message, bare.link) == (None, None, "", None)
    assert bare.tone == "info"
    assert bare.currency == "USD"


# ─── P2: GET /me/llm-budget ─────────────────────────────────────────────────


class _Recording(ILlmBudget):
    """Answers what it is told to, and records who it was asked about."""

    def __init__(self, answer=None, *, delay: float = 0.0, error: Exception | None = None):
        self.answer = answer
        self.delay = delay
        self.error = error
        self.asked: list[str] = []

    async def budget_for(self, request: Request, *, user_id: str) -> BudgetStatus | None:
        self.asked.append(user_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.answer


def _client(source: ILlmBudget | None, *, user: list[str] | None = None, clock=None, timeout=3.0):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from workspace_app.api.llm_budget import register_llm_budget_routes

    who = user if user is not None else ["alice"]
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_llm_budget_routes(
        api,
        llm_budget=source,
        get_user_id=lambda: who[0],
        timeout_s=timeout,
        **({"clock": clock} if clock else {}),
    )
    app.include_router(api)
    return TestClient(app)


def test_with_no_class_the_answer_is_off():
    body = _client(None).get("/api/me/llm-budget").json()
    assert body == {"status": "off", "budget": None, "stale_in_s": 0}


def test_the_class_answer_is_passed_through_for_the_person_asking():
    source = _Recording(
        BudgetStatus(
            remaining=11,
            total=20,
            currency="USD",
            message="Resets on the 1st.",
            link=BudgetLink(label="Ask for more", url="https://budget.corp/apply"),
            tone="warn",
        )
    )
    body = _client(source, user=["bob"]).get("/api/me/llm-budget?user_id=alice").json()
    assert body == {
        "status": "ok",
        "stale_in_s": 30,
        "budget": {
            "remaining": 11,
            "total": 20,
            "currency": "USD",
            "message": "Resets on the 1st.",
            "link": {"label": "Ask for more", "url": "https://budget.corp/apply"},
            "tone": "warn",
        },
    }
    # Whoever asks — never a user named in the query (only yourself).
    assert source.asked == ["bob"]


def test_nothing_to_show_is_ok_with_no_budget():
    body = _client(_Recording(None)).get("/api/me/llm-budget").json()
    assert body == {"status": "ok", "budget": None, "stale_in_s": 30}


def test_a_class_that_raises_is_unavailable_and_logged(caplog):
    source = _Recording(error=RuntimeError("cost system down"))
    with caplog.at_level("WARNING"):
        body = _client(source).get("/api/me/llm-budget").json()
    assert body == {"status": "unavailable", "budget": None, "stale_in_s": 30}
    assert "cost system down" in caplog.text


def test_a_class_slower_than_the_wait_is_unavailable():
    source = _Recording(BudgetStatus(remaining=1), delay=0.5)
    body = _client(source, timeout=0.05).get("/api/me/llm-budget").json()
    assert body["status"] == "unavailable" and body["budget"] is None


def test_one_person_is_asked_about_at_most_once_per_30_seconds():
    now = [1000.0]
    source = _Recording(BudgetStatus(remaining=11, total=20))
    who = ["alice"]
    client = _client(source, user=who, clock=lambda: now[0])
    client.get("/api/me/llm-budget")
    now[0] += 29
    client.get("/api/me/llm-budget")
    assert source.asked == ["alice"]
    who[0] = "bob"  # another person is their own question
    client.get("/api/me/llm-budget")
    assert source.asked == ["alice", "bob"]
    who[0] = "alice"
    now[0] += 2  # 31 s after alice's first ask
    client.get("/api/me/llm-budget")
    assert source.asked == ["alice", "bob", "alice"]


async def test_concurrent_asks_for_one_person_share_one_call():
    from workspace_app.api.llm_budget import BudgetCache

    source = _Recording(BudgetStatus(remaining=5), delay=0.05)
    cache = BudgetCache(source, timeout_s=3.0, clock=lambda: 0.0)
    nobody = cast(Request, None)
    results = await asyncio.gather(*(cache.status_for(nobody, user_id="alice") for _ in range(5)))
    assert source.asked == ["alice"]
    assert {r.status for r in results} == {"ok"}


def test_a_link_that_is_not_http_is_dropped():
    """The URL comes from the deploy's code and lands in an <a href>: a
    `javascript:` one would run in the person's page."""
    link = BudgetLink(label="x", url="javascript:alert(1)")
    body = (
        _client(_Recording(BudgetStatus(remaining=1, link=link))).get("/api/me/llm-budget").json()
    )
    assert body["budget"]["link"] is None


def test_create_app_serves_the_deploys_class():
    """Wired end to end: the instance `__main__` passes reaches the route."""
    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient

    source = _Recording(BudgetStatus(remaining=3, total=4))
    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        llm_budget=source,
        get_user_id=lambda: "carol",
    )
    with TestClient(app) as client:
        body = client.get("/api/me/llm-budget").json()
    assert body["status"] == "ok" and body["budget"]["remaining"] == 3
    assert source.asked == ["carol"]


# ─── review round 1 ─────────────────────────────────────────────────────────


def test_the_answer_says_when_the_server_will_ask_again():
    """A turn that ends inside the window gets the cached, pre-turn figure; the
    page can only fix that by asking again when the window ends — so the
    server, which holds the window, says when (the browser keeps no copy of
    the 30)."""
    now = [1000.0]
    client = _client(_Recording(BudgetStatus(remaining=1)), clock=lambda: now[0])
    assert client.get("/api/me/llm-budget").json()["stale_in_s"] == 30
    now[0] += 12
    assert client.get("/api/me/llm-budget").json()["stale_in_s"] == 18


def test_an_unavailable_answer_is_kept_for_the_window_too():
    now = [1000.0]
    source = _Recording(error=RuntimeError("down"))
    client = _client(source, clock=lambda: now[0])
    client.get("/api/me/llm-budget")
    now[0] += 29
    assert client.get("/api/me/llm-budget").json()["status"] == "unavailable"
    assert source.asked == ["alice"]


async def test_one_asker_giving_up_does_not_cancel_the_shared_ask():
    """A closed tab cancels its own request; the ask the others share must go on."""
    from workspace_app.api.llm_budget import BudgetCache

    source = _Recording(BudgetStatus(remaining=5), delay=0.05)
    cache = BudgetCache(source, timeout_s=3.0, clock=lambda: 0.0)
    nobody = cast(Request, None)
    first = asyncio.ensure_future(cache.status_for(nobody, user_id="alice"))
    second = asyncio.ensure_future(cache.status_for(nobody, user_id="alice"))
    await asyncio.sleep(0.01)
    first.cancel()
    assert (await second).status == "ok"
    assert source.asked == ["alice"]


def test_a_link_with_no_label_is_dropped():
    """It would draw as nothing — focusable, with no name."""
    link = BudgetLink(label="  ", url="https://budget.corp/apply")
    body = (
        _client(_Recording(BudgetStatus(remaining=1, link=link))).get("/api/me/llm-budget").json()
    )
    assert body["budget"]["link"] is None
