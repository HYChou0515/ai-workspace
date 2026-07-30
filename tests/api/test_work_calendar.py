"""#615 P1 — the deployment's work calendar (which days people are in the office).

Read by anyone (the off-hours sweeper and the goal panel both need to know when
unattended work may run); written by a superuser only. The overrides map is
deliberately two-way: `off` frees a weekday (a public holiday), `work` claims a
weekend day (Taiwan's flexible-holiday make-up workdays).
"""

from datetime import datetime

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.work_calendar import read_work_calendar
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workcalendar import OffHoursCalendar

from ._client import TestClient

ROOT = frozenset({"root"})
URL = "/api/work-calendar"


def _client(holder: dict[str, str]) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"], superusers=ROOT)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=ROOT,
    )
    return TestClient(app), spec


def test_unconfigured_deployment_reads_as_a_monday_to_friday_calendar() -> None:
    # The panel has to render before anyone has configured anything, so an
    # absent row is a default calendar, not a 404.
    client, _ = _client({"id": "alice"})
    body = client.get(URL).json()
    assert body["workdays"] == [0, 1, 2, 3, 4]
    assert body["overrides"] == {}


def test_superuser_records_a_makeup_workday_and_everyone_reads_it() -> None:
    # The whole reason this is a calendar: a Saturday people actually work.
    # Anyone may read it — the sweeper on every pod has to agree about today.
    holder = {"id": "root"}
    client, _ = _client(holder)
    saved = client.put(URL, json={"workdays": [0, 1, 2, 3, 4], "overrides": {"2026-08-01": "work"}})
    assert saved.status_code == 200

    holder["id"] = "alice"
    body = client.get(URL).json()
    assert body["overrides"] == {"2026-08-01": "work"}


def test_editing_the_calendar_again_replaces_it() -> None:
    # Holidays are corrected and re-published; the second save must land, not
    # collide with the row the first one created.
    client, _ = _client({"id": "root"})
    client.put(URL, json={"workdays": [0, 1, 2, 3, 4], "overrides": {"2026-08-01": "work"}})
    client.put(URL, json={"workdays": [0, 1, 2, 3], "overrides": {"2026-01-01": "off"}})

    body = client.get(URL).json()
    assert body["workdays"] == [0, 1, 2, 3]
    assert body["overrides"] == {"2026-01-01": "off"}


def test_non_superuser_cannot_edit() -> None:
    # One row decides when an unattended agent may touch everybody's chats.
    # (The FE hides the editor via `useIsSuperuser`; this is the real gate.)
    client, _ = _client({"id": "alice"})
    assert client.put(URL, json={"workdays": [0], "overrides": {}}).status_code == 403


def test_a_typo_is_refused_at_the_edit_not_silently_stored() -> None:
    # The editor is free text. A malformed entry that reached storage would not
    # fail loudly later — it would quietly move somebody's off-hours window.
    client, _ = _client({"id": "root"})
    bad_date = client.put(URL, json={"workdays": [0], "overrides": {"2026-13-99": "off"}})
    assert bad_date.status_code == 400
    assert "2026-13-99" in bad_date.json()["detail"]

    bad_value = client.put(URL, json={"workdays": [0], "overrides": {"2026-08-01": "holiday"}})
    assert bad_value.status_code == 400
    assert "holiday" in bad_value.json()["detail"]

    bad_day = client.put(URL, json={"workdays": [9], "overrides": {}})
    assert bad_day.status_code == 400

    # None of it landed: the calendar is still the untouched default.
    assert client.get(URL).json()["overrides"] == {}


def test_a_stored_calendar_decides_the_off_hours_answer() -> None:
    # The two halves of P1 wired together: what a superuser saves is what the
    # sweeper's `is_offhours` will answer with. Saturday 2026-08-01 is recorded
    # as a make-up workday, so 10:00 that day is office hours — while the
    # Sunday after it is free all day.
    client, spec = _client({"id": "root"})
    client.put(URL, json={"workdays": [0, 1, 2, 3, 4], "overrides": {"2026-08-01": "work"}})

    stored = read_work_calendar(spec)
    cal = OffHoursCalendar(
        window="19:00-08:00",
        timezone="Asia/Taipei",
        workdays=tuple(stored.workdays),
        overrides=stored.overrides,
    )
    assert cal.is_offhours(datetime(2026, 8, 1, 10, 0)) is False
    assert cal.is_offhours(datetime(2026, 8, 2, 10, 0)) is True
