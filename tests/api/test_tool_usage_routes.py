"""The administrator's report (docs/plan-tool-usage-stats.md P5): superusers
only; 7 / 30 / 90 days; per tool, then one tool's breakdown by person, by
surface × App, by item."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from workspace_app.agent.tool_usage import ToolUsageRecorder
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.tool_usage_store import ToolUsageStore, register_tool_usage
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

TODAY = dt.datetime.now(dt.UTC).date()


def _day(back: int) -> str:
    return (TODAY - dt.timedelta(days=back)).isoformat()


def _app(holder: dict[str, str]):  # noqa: ANN202
    spec = make_spec(default_user=lambda: holder["id"], superusers=frozenset({"root"}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=frozenset({"root"}),
    )
    register_tool_usage(spec)
    return TestClient(app), spec


def _seed(
    spec,
    back: int,
    tool: str,
    source: str,
    *,
    failed=False,
    ms=100,
    user="alice",  # noqa: ANN001
    surface="chat",
    app="rca",
    item="i1",
) -> None:
    rec = ToolUsageRecorder(day=lambda: _day(back))
    rec.record(tool, source, failed=failed, ms=ms, user=user, surface=surface, app=app, item=item)
    ToolUsageStore(spec, rec, pod="p", today=lambda: _day(back)).flush()


def test_only_a_superuser_may_read_it() -> None:
    holder = {"id": "alice"}
    client, _ = _app(holder)

    assert client.get("/admin/tool-usage").status_code == 403
    assert client.get("/admin/tool-usage/spc").status_code == 403
    holder["id"] = "root"
    assert client.get("/admin/tool-usage").status_code == 200


@pytest.mark.parametrize("days", [1, 14, 365])
def test_the_window_is_seven_thirty_or_ninety_days(days: int) -> None:
    client, _ = _app({"id": "root"})

    assert client.get(f"/admin/tool-usage?days={days}").status_code == 422


def test_per_tool_calls_failures_and_average_time() -> None:
    client, spec = _app({"id": "root"})
    _seed(spec, 0, "spc", "first_party", ms=100)
    _seed(spec, 1, "spc", "first_party", failed=True, ms=300)
    _seed(spec, 0, "exec", "builtin", ms=10)

    body = client.get("/admin/tool-usage").json()

    rows = {r["tool"]: r for r in body["tools"]}
    assert rows["spc"] == {
        "tool": "spc",
        "source": "first_party",
        "calls": 2,
        "fails": 1,
        "avg_ms": 200,
    }
    assert rows["exec"]["calls"] == 1
    assert body["days"] == 30


def test_the_window_counts_only_its_days() -> None:
    client, spec = _app({"id": "root"})
    _seed(spec, 0, "spc", "first_party")
    _seed(spec, 10, "spc", "first_party")
    _seed(spec, 40, "spc", "first_party")

    def calls(days: int) -> int:
        [row] = client.get(f"/admin/tool-usage?days={days}").json()["tools"]
        return row["calls"]

    assert (calls(7), calls(30), calls(90)) == (1, 2, 3)


def test_pods_and_days_of_one_tool_add_up_to_one_row() -> None:
    client, spec = _app({"id": "root"})
    for pod in ("a", "b"):
        rec = ToolUsageRecorder(day=lambda: _day(0))
        rec.record(
            "spc", "first_party", failed=False, ms=10, user="u", surface="chat", app="rca", item="i"
        )
        ToolUsageStore(spec, rec, pod=pod, today=lambda: _day(0)).flush()

    [row] = client.get("/admin/tool-usage").json()["tools"]
    assert row["calls"] == 2


def test_filters_by_source_surface_and_app() -> None:
    client, spec = _app({"id": "root"})
    _seed(spec, 0, "spc", "first_party", surface="chat", app="rca")
    _seed(spec, 0, "spc", "first_party", surface="schedule", app="pm", failed=True)
    _seed(spec, 0, "acme__lookup", "third_party", surface="chat", app="rca")

    def tools(q: str) -> dict[str, tuple[int, int]]:
        return {
            r["tool"]: (r["calls"], r["fails"])
            for r in client.get(f"/admin/tool-usage?{q}").json()["tools"]
        }

    assert tools("source=third_party") == {"acme__lookup": (1, 0)}
    assert tools("surface=schedule") == {"spc": (1, 1)}
    assert tools("app=rca") == {"spc": (1, 0), "acme__lookup": (1, 0)}
    assert tools("surface=chat&app=pm") == {}


def test_one_tool_by_person_by_surface_and_app_by_item() -> None:
    client, spec = _app({"id": "root"})
    iid = register_rca_item(spec, title="Line 3")
    _seed(spec, 0, "spc", "first_party", user="alice", surface="chat", app="rca", item=iid, ms=100)
    _seed(
        spec,
        0,
        "spc",
        "first_party",
        user="bob",
        surface="schedule",
        app="rca",
        item=iid,
        failed=True,
        ms=300,
    )

    body = client.get("/admin/tool-usage/spc").json()

    assert body["tool"] == "spc"
    assert body["by_user"] == [
        {"user": "alice", "calls": 1, "fails": 0, "avg_ms": 100},
        {"user": "bob", "calls": 1, "fails": 1, "avg_ms": 300},
    ]
    assert {(r["surface"], r["app"], r["calls"]) for r in body["by_surface_app"]} == {
        ("chat", "rca", 1),
        ("schedule", "rca", 1),
    }
    assert body["by_item"] == [
        {"item": iid, "title": "Line 3", "slug": "rca", "calls": 2, "fails": 1, "avg_ms": 200},
    ]


def test_an_unknown_tool_has_an_empty_breakdown() -> None:
    client, _ = _app({"id": "root"})

    body = client.get("/admin/tool-usage/nope").json()

    assert body == {"tool": "nope", "days": 30, "by_user": [], "by_surface_app": [], "by_item": []}


# ── skills (plan §8.3) ──────────────────────────────────────────────────────


def _seed_skill(spec, back: int, name: str, source: str, *, user="alice", item="i1") -> None:  # noqa: ANN001
    rec = ToolUsageRecorder(day=lambda: _day(back))
    rec.record(
        name,
        source,
        kind="skill",
        failed=False,
        ms=0,
        user=user,
        surface="chat",
        app="rca",
        item=item,
    )
    ToolUsageStore(spec, rec, pod="p", today=lambda: _day(back)).flush()


def test_the_report_reads_one_kind_and_tools_by_default() -> None:
    client, spec = _app({"id": "root"})
    _seed(spec, 1, "exec", "builtin")
    _seed_skill(spec, 1, "report-format", "profile")

    tools = client.get("/admin/tool-usage").json()["tools"]
    skills = client.get("/admin/tool-usage?kind=skill").json()["tools"]

    assert [t["tool"] for t in tools] == ["exec"]  # a caller that names no kind is unchanged
    assert [(t["tool"], t["source"], t["calls"]) for t in skills] == [
        ("report-format", "profile", 1)
    ]


def test_one_skill_breaks_down_by_person_and_item() -> None:
    client, spec = _app({"id": "root"})
    _seed_skill(spec, 1, "fmt", "hub", user="bob", item="i9")
    _seed(spec, 1, "fmt", "builtin", user="carol")  # a TOOL of the same name

    d = client.get("/admin/tool-usage/fmt?kind=skill").json()

    assert [u["user"] for u in d["by_user"]] == ["bob"]
    assert [i["item"] for i in d["by_item"]] == ["i9"]


@pytest.mark.parametrize("path", ["/admin/tool-usage", "/admin/tool-usage/x"])
def test_a_kind_that_is_not_tool_or_skill_is_refused(path: str) -> None:
    client, _spec = _app({"id": "root"})

    assert client.get(f"{path}?kind=mcp").status_code == 422
