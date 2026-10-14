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
        "package": "",  # seeded without one
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


# ── granularity and raw data (plan §9) ─────────────────────────────────────


def _seed_pkg(spec, back: int, tool: str, package: str, *, fails=0, ms=100, user="alice", pod="p"):  # noqa: ANN001
    rec = ToolUsageRecorder(day=lambda: _day(back))
    for i in range(1 + fails):
        rec.record(
            tool,
            "third_party",
            package=package,
            failed=i < fails,
            ms=ms,
            user=user,
            surface="chat",
            app="rca",
            item="i1",
        )
    ToolUsageStore(spec, rec, pod=pod, today=lambda: _day(back)).flush()


def test_by_package_a_packages_commands_are_one_row_and_built_ins_stay_apart() -> None:
    client, spec = _app({"id": "root"})
    _seed_pkg(spec, 1, "acme__ls", "acme")  # 1 call
    _seed_pkg(spec, 1, "acme__cat", "acme", fails=1)  # 2 calls, 1 failed
    _seed(spec, 1, "exec", "builtin")
    _seed(spec, 1, "read_file", "builtin")

    by_command = client.get("/admin/tool-usage").json()["tools"]
    by_package = client.get("/admin/tool-usage?group=package").json()["tools"]

    assert sorted(t["tool"] for t in by_command) == ["acme__cat", "acme__ls", "exec", "read_file"]
    got = {t["tool"]: (t["source"], t["calls"], t["fails"]) for t in by_package}
    assert got == {
        "acme": ("third_party", 3, 1),
        "exec": ("builtin", 1, 0),
        "read_file": ("builtin", 1, 0),
    }


def test_one_package_breaks_down_over_all_its_commands() -> None:
    client, spec = _app({"id": "root"})
    _seed_pkg(spec, 1, "acme__ls", "acme", user="bob")
    _seed_pkg(spec, 1, "acme__cat", "acme", user="carol")

    d = client.get("/admin/tool-usage/acme?group=package").json()

    assert sorted((u["user"], u["calls"]) for u in d["by_user"]) == [("bob", 1), ("carol", 1)]


@pytest.mark.parametrize("path", ["/admin/tool-usage", "/admin/tool-usage/x"])
def test_a_group_that_is_not_command_or_package_is_refused(path: str) -> None:
    client, _spec = _app({"id": "root"})

    assert client.get(f"{path}?group=team").status_code == 422


def _csv(client, query: str) -> list[dict[str, str]]:  # noqa: ANN001
    import csv
    import io

    r = client.get(f"/admin/tool-usage.csv?{query}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # Excel reads UTF-8 by its BOM
    return list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))


def test_the_raw_csv_has_every_stored_cell_one_per_line_processes_added() -> None:
    client, spec = _app({"id": "root"})
    _seed_pkg(spec, 1, "acme__ls", "acme", fails=1, ms=10, pod="a")  # 2 calls on pod a
    _seed_pkg(spec, 1, "acme__ls", "acme", ms=10, pod="b")  # 1 more on pod b

    rows = _csv(client, "days=7")

    total = [r for r in rows if r["dimension"] == "total"]
    assert total == [
        {
            "day": _day(1),
            "kind": "tool",
            "name": "acme__ls",
            "package": "acme",
            "source": "third_party",
            "dimension": "total",
            "user": "",
            "surface": "",
            "app": "",
            "item": "",
            "calls": "3",
            "fails": "1",
            "total_ms": "30",
        }
    ]
    assert [(r["user"], r["calls"]) for r in rows if r["dimension"] == "user"] == [("alice", "3")]
    assert [(r["surface"], r["app"]) for r in rows if r["dimension"] == "surface_app"] == [
        ("chat", "rca")
    ]
    assert [r["item"] for r in rows if r["dimension"] == "item"] == ["i1"]


def test_the_raw_csv_is_one_kind_and_one_window() -> None:
    client, spec = _app({"id": "root"})
    _seed(spec, 1, "exec", "builtin")
    _seed(spec, 20, "old", "builtin")
    _seed_skill(spec, 1, "fmt", "hub")

    tools = {r["name"] for r in _csv(client, "days=7")}
    skills = {(r["name"], r["kind"]) for r in _csv(client, "days=7&kind=skill")}

    assert tools == {"exec"}  # 20 days ago is outside 7
    assert skills == {("fmt", "skill")}


def test_only_a_superuser_may_download_it() -> None:
    client, _spec = _app({"id": "alice"})

    assert client.get("/admin/tool-usage.csv").status_code == 403


def test_one_name_from_two_sources_breaks_down_one_source_at_a_time() -> None:
    """A skill installed from the hub can share its name with one written in
    another item (§8.2): a breakdown asks for the row's source, or it adds the
    two together."""
    client, spec = _app({"id": "root"})
    _seed_skill(spec, 1, "line-report", "workspace", user="bob")
    _seed_skill(spec, 1, "line-report", "hub", user="carol")

    both = client.get("/admin/tool-usage/line-report?kind=skill").json()
    hub = client.get("/admin/tool-usage/line-report?kind=skill&source=hub").json()

    assert sorted(u["user"] for u in both["by_user"]) == ["bob", "carol"]
    assert [u["user"] for u in hub["by_user"]] == ["carol"]


def test_each_command_says_which_package_it_belongs_to() -> None:
    """§9.2: with a hundred commands the picker groups them by package, so a
    report line names its package; a built-in names none."""
    client, spec = _app({"id": "root"})
    _seed_pkg(spec, 1, "acme__ls", "acme")
    _seed(spec, 1, "exec", "builtin")

    got = {t["tool"]: t["package"] for t in client.get("/admin/tool-usage").json()["tools"]}

    assert got == {"acme__ls": "acme", "exec": ""}


def test_one_command_name_in_two_packages_breaks_down_one_package_at_a_time() -> None:
    client, spec = _app({"id": "root"})
    _seed_pkg(spec, 1, "summarize", "pkgA", user="bob")
    _seed_pkg(spec, 1, "summarize", "pkgB", user="carol")

    lines = client.get("/admin/tool-usage").json()["tools"]
    b = client.get("/admin/tool-usage/summarize?source=third_party&package=pkgB").json()

    assert sorted((t["tool"], t["package"]) for t in lines) == [
        ("summarize", "pkgA"),
        ("summarize", "pkgB"),
    ]
    assert [u["user"] for u in b["by_user"]] == ["carol"]


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_the_raw_csv_never_hands_excel_a_formula(lead: str) -> None:
    """A skill name is the user's (a `.skill/<name>/` folder): one that starts
    like a formula must reach the administrator's spreadsheet as text — OWASP's
    CSV injection advice, a leading apostrophe (review r3)."""
    client, spec = _app({"id": "root"})
    _seed_skill(spec, 1, f'{lead}HYPERLINK("http://e","x")', "workspace")

    [total] = [r for r in _csv(client, "days=7&kind=skill") if r["dimension"] == "total"]

    assert total["name"] == f'\'{lead}HYPERLINK("http://e","x")'
