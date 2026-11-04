"""P5 of `docs/plan-wui-usage.md`: the administrator's report of every deployed
page (U8) — 7 / 30 / 90 days or every day (U12), a page's people, and the raw
cells as CSV. Superusers only (U5).
"""

from __future__ import annotations

import datetime as dt

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.wui_usage import WuiUsageRecorder, WuiUsageStore
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

PAGE = "/p/page.ai.yaml"
ROOT = "root"


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _world():
    holder = {"id": ROOT}
    spec = make_spec(default_user=lambda: holder["id"], superusers=frozenset({ROOT}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=frozenset({ROOT}),
    )
    return TestClient(app), holder, spec


def _count(spec, *, days_ago: int, item: str = "i1", page: str = PAGE, **cells) -> None:
    """Write one day's counts straight to the store, as a past flush would."""
    day = (_today() - dt.timedelta(days=days_ago)).isoformat()
    rec = WuiUsageRecorder(today=lambda: day)
    store = WuiUsageStore(spec, rec, today=lambda: day)
    for user, (opens, uses) in cells.items():
        for _ in range(opens):
            rec.opened(item, page, user=user)
        for kind, ms in uses:
            rec.used(item, page, user=user, kind=kind, ms=ms)
    store.flush()


def test_only_a_superuser_reads_the_report():
    client, holder, _spec = _world()
    holder["id"] = "alice"

    assert client.get("/admin/wui-usage").status_code == 403
    assert client.get("/admin/wui-usage.csv").status_code == 403
    assert (
        client.get("/admin/wui-usage/detail", params={"item_id": "i1", "page": PAGE}).status_code
        == 403
    )


def test_a_window_is_seven_thirty_ninety_days_or_every_day():
    client, _holder, _spec = _world()

    assert client.get("/admin/wui-usage", params={"days": 14}).status_code == 422
    for days in (7, 30, 90, 0):
        assert client.get("/admin/wui-usage", params={"days": days}).status_code == 200


def test_each_page_is_one_line_of_person_days_and_each_kind_s_uses_and_time():
    client, _holder, spec = _world()
    _count(spec, days_ago=0, alice=(2, [("tool", 1000), ("agent", 3000)]), bob=(1, []))
    _count(spec, days_ago=1, alice=(1, [("workflow", 60_000)]))
    _count(spec, days_ago=40, alice=(1, [("tool", 5)]))

    week = client.get("/admin/wui-usage", params={"days": 7}).json()
    every = client.get("/admin/wui-usage", params={"days": 0}).json()

    (line,) = week["pages"]
    assert (line["item_id"], line["page"]) == ("i1", PAGE)
    assert line["person_days"] == 3  # alice and bob today, alice yesterday
    assert (line["workflow"], line["workflow_ms"]) == (1, 60_000)
    assert (line["tool"], line["tool_ms"]) == (1, 1000)
    assert (line["agent"], line["agent_ms"]) == (1, 3000)
    assert (line["uses"], line["uses_ms"]) == (3, 64_000)
    (all_line,) = every["pages"]
    assert all_line["person_days"] == 4
    assert all_line["tool"] == 2


def test_pages_are_listed_busiest_first():
    client, _holder, spec = _world()
    _count(spec, days_ago=0, page="/quiet/page.ai.yaml", alice=(1, []))
    _count(spec, days_ago=0, page="/busy/page.ai.yaml", alice=(1, []), bob=(1, []))

    pages = client.get("/admin/wui-usage", params={"days": 7}).json()["pages"]

    assert [p["page"] for p in pages] == ["/busy/page.ai.yaml", "/quiet/page.ai.yaml"]


def test_a_page_s_detail_is_its_people():
    client, _holder, spec = _world()
    _count(spec, days_ago=0, alice=(3, [("tool", 100)]), bob=(1, [("agent", 50)]))
    _count(spec, days_ago=2, alice=(1, []))

    r = client.get("/admin/wui-usage/detail", params={"item_id": "i1", "page": PAGE, "days": 30})

    assert r.status_code == 200, r.text
    people = {p["user"]: p for p in r.json()["people"]}
    assert (people["alice"]["person_days"], people["alice"]["opens"]) == (2, 4)
    assert (people["alice"]["tool"], people["alice"]["tool_ms"]) == (1, 100)
    assert (people["bob"]["agent"], people["bob"]["agent_ms"]) == (1, 50)
    assert [p["user"] for p in r.json()["people"]] == ["alice", "bob"]


def test_the_csv_is_every_person_s_day_and_no_cell_is_a_formula():
    client, _holder, spec = _world()
    _count(spec, days_ago=0, page="=cmd()/page.ai.yaml", alice=(1, [("tool", 10)]))

    r = client.get("/admin/wui-usage.csv", params={"days": 7})

    assert r.status_code == 200
    assert r.content.startswith(b"\xef\xbb\xbf")  # a BOM: Excel reads it as UTF-8
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0].split(",")[:4] == ["day", "item_id", "page", "user"]
    assert "'=cmd()/page.ai.yaml" in lines[1]
    assert lines[1].split(",")[3] == "alice"
