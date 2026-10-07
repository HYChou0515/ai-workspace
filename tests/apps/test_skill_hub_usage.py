"""How many times each skill hub entry was installed and used
(docs/plan-skill-hub-history.md §4.8, U1–U5).

Counting is a dict increment in this pod's memory — nothing on the request
path waits for storage. A flush adds what accumulated to this pod's row for
the day and keeps one revision of it; the total is a sum over the entry's rows.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.apps.skill_hub_usage import (
    SkillHubUsage,
    UsageCounter,
    register_skill_hub_usage,
)
from workspace_app.resources import make_spec


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="system")
    register_skill_hub_usage(s)
    return s


def _counter(spec: SpecStar, pod: str, day: str = "2026-10-07") -> UsageCounter:
    return UsageCounter(spec, pod=pod, today=lambda: day)


async def test_counts_reach_storage_only_on_a_flush(spec: SpecStar) -> None:
    a = _counter(spec, "pod-a")
    a.install("e1", user="bob", item="i1")
    a.use("e1", user="bob", item="i1")
    a.use("e1", user="carol", item="i2")

    assert a.totals(["e1"]) == {"e1": (0, 0)}, "nothing is written on the request path"
    await a.flush()
    assert a.totals(["e1", "e2"]) == {"e1": (1, 2), "e2": (0, 0)}


async def test_two_pods_and_two_days_are_separate_rows_that_add_up(spec: SpecStar) -> None:
    a, b = _counter(spec, "pod-a"), _counter(spec, "pod-b")
    a.install("e1", user="bob", item="i1")
    b.install("e1", user="carol", item="i2")
    b.use("e1", user="carol", item="i2")
    await a.flush()
    await b.flush()
    tomorrow = _counter(spec, "pod-a", day="2026-10-08")
    tomorrow.use("e1", user="bob", item="i1")
    await tomorrow.flush()

    assert a.totals(["e1"]) == {"e1": (2, 2)}
    rows = spec.get_resource_manager(SkillHubUsage).list_resources(returns=["data"])
    assert sorted((r.data.pod, r.data.day) for r in rows) == [  # ty: ignore[unresolved-attribute]
        ("pod-a", "2026-10-07"),
        ("pod-a", "2026-10-08"),
        ("pod-b", "2026-10-07"),
    ]


async def test_a_second_flush_adds_to_the_row_and_keeps_one_revision(spec: SpecStar) -> None:
    """U3: a counter row that kept every revision would grow by one per flush."""
    a = _counter(spec, "pod-a")
    a.use("e1", user="bob", item="i1")
    await a.flush()
    a.use("e1", user="bob", item="i1")
    a.use("e1", user="bob", item="i3")
    await a.flush()
    await a.flush()  # nothing new: no write at all

    rm = spec.get_resource_manager(SkillHubUsage)
    (row,) = rm.list_resources(returns=["info", "data"])
    assert row.data.uses == 3  # ty: ignore[unresolved-attribute]
    assert len(rm.list_revisions(row.info.resource_id)) == 1  # ty: ignore[unresolved-attribute]
    # U5: who and where are recorded, never shown.
    per = row.data.by_user_item  # ty: ignore[unresolved-attribute]
    assert {k: v.uses for k, v in per.items()} == {"bob∕i1": 2, "bob∕i3": 1}


async def test_a_flush_that_fails_keeps_the_counts_for_the_next(spec: SpecStar) -> None:
    a = _counter(spec, "pod-a")
    a.install("e1", user="bob", item="i1")
    rm = spec.get_resource_manager(SkillHubUsage)
    real = rm.create

    def broken(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("database down")

    rm.create = broken  # ty: ignore[invalid-assignment]
    with pytest.raises(RuntimeError):
        await a.flush()
    rm.create = real  # ty: ignore[invalid-assignment]
    await a.flush()

    assert a.totals(["e1"]) == {"e1": (1, 0)}
