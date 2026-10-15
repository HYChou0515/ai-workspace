"""The usage report on Postgres (the person: 「Tool usage 回覆500」).

`day` is an ISO date string, and specstar's Postgres meta store compares a
range on an indexed field as a number — `(indexed_data->>'day')::numeric <
'2026-07-17'` — so every report (and the daily prune) failed there with
`invalid input syntax for type numeric`, while the in-memory backend the unit
tests run on compared the strings and passed. The store now asks for the days
it means by name: membership, which Postgres compares as text.
"""

from __future__ import annotations

import os
import uuid

import pytest
from specstar import QB

from tests.api._client import TestClient
from workspace_app.agent.tool_usage import ToolUsageRecorder
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.tool_usage_store import (
    ToolUsageDay,
    ToolUsageStore,
    register_tool_usage,
    rows_since,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

_RANGE = {"gt", "gte", "lt", "lte", "between", "in_range"}


def _conditions(node: object):  # noqa: ANN202 — walks specstar's query tree
    for cond in getattr(node, "conditions", None) or []:
        if hasattr(cond, "field_path"):
            yield cond
        yield from _conditions(cond)


class _Spy:
    """A resource manager that records the queries it is asked."""

    def __init__(self, rm: object) -> None:
        self._rm = rm
        self.asked: list[object] = []

    def __getattr__(self, name: str):  # noqa: ANN204
        attr = getattr(self._rm, name)
        if name in ("list_resources", "search_resources"):

            def asked(query=None, **kw):  # noqa: ANN001, ANN202, ANN003
                self.asked.append(query)
                return attr(query, **kw)

            return asked
        return attr


# CI's guard (no Postgres there): what the store asks of `day` is never a
# range — the one comparison Postgres turns into a number.
def test_the_store_never_asks_a_range_of_the_day(monkeypatch) -> None:  # noqa: ANN001
    spec = make_spec()
    register_tool_usage(spec)
    spy = _Spy(spec.get_resource_manager(ToolUsageDay))
    monkeypatch.setattr(spec, "get_resource_manager", lambda model: spy)

    rows_since(spec, "2026-09-15", kind="tool")
    rows_since(spec, "2026-09-15", kind="skill", tool="t", package="p")
    ToolUsageStore(spec, ToolUsageRecorder(), pod="p", today=lambda: "2026-10-15").prune()

    assert spy.asked
    on_day = [c for q in spy.asked for c in _conditions(q) if c.field_path == "day"]
    assert on_day, "the day is what every one of these asks about"
    assert {c.operator.value for c in on_day}.isdisjoint(_RANGE), on_day


# ── on a real Postgres (local; CI has none) ──


@pytest.fixture
def pg_spec(tmp_path):  # noqa: ANN001, ANN201
    dsn = os.environ.get("WORKSPACE_TEST_PG_DSN")
    if not dsn:
        pytest.skip("set WORKSPACE_TEST_PG_DSN to a Postgres this test may create a database on")
    import psycopg2
    from specstar import BackendBinding, BackendConfig, ConnectionProfile

    name = f"usage_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name}")
    db = dsn.rsplit("/", 1)[0] + "/" + name
    backend = BackendConfig(
        connections={
            "pg": ConnectionProfile(type="postgres", options={"connection_string": db}),
            "local": ConnectionProfile(type="disk", options={"rootdir": str(tmp_path)}),
        },
        meta=BackendBinding(use="pg"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )
    try:
        yield make_spec(default_user="admin", backend=backend, superusers=frozenset({"admin"}))
    finally:
        from specstar.resource_manager import _pg_pool

        for close in (getattr(_pg_pool, "close_all", None),):
            if close is not None:
                close()
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


@pytest.mark.integration
def test_the_report_answers_on_postgres(pg_spec) -> None:  # noqa: ANN001
    app = create_app(
        spec=pg_spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: "admin",
        superusers=frozenset({"admin"}),
    )
    rec = ToolUsageRecorder()
    rec.record(
        "read_file",
        "built-in",
        failed=False,
        ms=12,
        user="admin",
        surface="chat",
        app="rca",
        item="i1",
    )
    ToolUsageStore(pg_spec, rec, pod="p").flush()
    client = TestClient(app)

    report = client.get("/api/admin/tool-usage?days=30")
    detail = client.get("/api/admin/tool-usage/read_file?days=30")
    csv = client.get("/api/admin/tool-usage.csv?days=30")

    assert report.status_code == 200, report.text
    assert [t["tool"] for t in report.json()["tools"]] == ["read_file"]
    assert detail.status_code == 200 and detail.json()["calls"] == 1, detail.text
    assert csv.status_code == 200 and "read_file" in csv.text


@pytest.mark.integration
def test_the_prune_works_on_postgres(pg_spec) -> None:  # noqa: ANN001
    register_tool_usage(pg_spec)
    for day in ("2026-07-15", "2026-07-16", "2026-10-14"):
        rec = ToolUsageRecorder(day=lambda d=day: d)
        rec.record(
            "spc", "first_party", failed=False, ms=1, user="a", surface="chat", app="rca", item="i"
        )
        ToolUsageStore(pg_spec, rec, pod="p", today=lambda d=day: d).flush()

    ToolUsageStore(pg_spec, ToolUsageRecorder(), pod="p", today=lambda: "2026-10-14").prune()

    rm = pg_spec.get_resource_manager(ToolUsageDay)
    days = sorted(r.data.day for r in rm.list_resources(QB.all()))
    assert days == ["2026-07-16", "2026-10-14"]


# A prune deletes only expired days, never a newer one: a pod whose clock runs
# behind (here, by 40 days) must not delete what the others wrote since.
def test_a_prune_never_deletes_a_newer_day() -> None:
    spec = make_spec()
    register_tool_usage(spec)
    for day in ("2026-08-20", "2026-10-14"):
        rec = ToolUsageRecorder(day=lambda d=day: d)
        rec.record(
            "spc", "first_party", failed=False, ms=1, user="a", surface="chat", app="rca", item="i"
        )
        ToolUsageStore(spec, rec, pod="p", today=lambda d=day: d).flush()

    ToolUsageStore(spec, ToolUsageRecorder(), pod="slow", today=lambda: "2026-09-04").prune()

    rm = spec.get_resource_manager(ToolUsageDay)
    assert sorted(r.data.day for r in rm.list_resources(QB.all())) == ["2026-08-20", "2026-10-14"]
