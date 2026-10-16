"""Writing the counts down, and letting them go after 90 days
(docs/plan-tool-usage-stats.md P4).

Each pod adds what it counted to ITS OWN row per tool per day — the row id
carries the pod — so two pods never write the same row (skill hub's shape).
"""

from __future__ import annotations

import threading

import pytest
from specstar import QB

from workspace_app.agent.tool_usage import ToolUsageRecorder
from workspace_app.api.tool_usage_store import (
    KEEP_DAYS,
    ToolUsageDay,
    ToolUsageStore,
    register_tool_usage,
    run_flusher,
)
from workspace_app.resources import make_spec


def _spec():
    spec = make_spec()
    register_tool_usage(spec)
    return spec


def _rows(spec) -> list[ToolUsageDay]:  # noqa: ANN001
    out = []
    for r in spec.get_resource_manager(ToolUsageDay).list_resources(QB.all()):
        assert isinstance(r.data, ToolUsageDay)
        out.append(r.data)
    return out


def _count(rec: ToolUsageRecorder, *, tool="spc", user="alice", failed=False, ms=10) -> None:
    rec.record(
        tool, "first_party", failed=failed, ms=ms, user=user, surface="chat", app="rca", item="i1"
    )


def test_a_flush_writes_this_pod_s_counts_and_empties_its_memory() -> None:
    spec = _spec()
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    store = ToolUsageStore(spec, rec, pod="pod-a", today=lambda: "2026-10-14")
    _count(rec)
    _count(rec, failed=True, ms=30)

    store.flush()

    [row] = _rows(spec)
    assert (row.tool, row.source, row.day, row.pod) == ("spc", "first_party", "2026-10-14", "pod-a")
    assert (row.calls, row.fails, row.total_ms) == (2, 1, 40)
    assert row.by_user == {"alice": [2, 1, 40]}
    assert rec.drain() == []


def test_a_second_flush_adds_to_the_same_row() -> None:
    spec = _spec()
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    store = ToolUsageStore(spec, rec, pod="pod-a", today=lambda: "2026-10-14")
    _count(rec)
    store.flush()
    _count(rec, user="bob")
    store.flush()

    [row] = _rows(spec)
    assert row.calls == 2
    assert row.by_user == {"alice": [1, 0, 10], "bob": [1, 0, 10]}


def test_two_pods_never_write_one_row() -> None:
    spec = _spec()
    for pod in ("pod-a", "pod-b"):
        rec = ToolUsageRecorder(day=lambda: "2026-10-14")
        _count(rec)
        ToolUsageStore(spec, rec, pod=pod, today=lambda: "2026-10-14").flush()

    assert sorted(r.pod for r in _rows(spec)) == ["pod-a", "pod-b"]


def test_a_failed_write_keeps_the_counts_for_the_next_flush(monkeypatch) -> None:  # noqa: ANN001
    spec = _spec()
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    store = ToolUsageStore(spec, rec, pod="pod-a", today=lambda: "2026-10-14")
    _count(rec)
    real = store._add  # noqa: SLF001

    def boom(_row):  # noqa: ANN001, ANN202
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "_add", boom)
    with pytest.raises(RuntimeError):
        store.flush()
    monkeypatch.setattr(store, "_add", real)
    store.flush()

    [row] = _rows(spec)
    assert row.calls == 1


def test_rows_older_than_ninety_days_go_and_the_rest_stay() -> None:
    spec = _spec()
    assert KEEP_DAYS == 90
    for day in ("2026-07-15", "2026-07-16", "2026-10-14"):
        rec = ToolUsageRecorder(day=lambda d=day: d)
        _count(rec)
        ToolUsageStore(spec, rec, pod="p", today=lambda d=day: d).flush()

    store = ToolUsageStore(spec, ToolUsageRecorder(), pod="p", today=lambda: "2026-10-14")
    store.prune()

    # 2026-10-14 minus 90 days is 2026-07-16: kept; the day before is gone.
    assert sorted(r.day for r in _rows(spec)) == ["2026-07-16", "2026-10-14"]


def test_the_prune_runs_once_a_day_from_the_flush() -> None:
    spec = _spec()
    days = iter(["2026-10-14", "2026-10-14", "2026-10-15"])
    store = ToolUsageStore(spec, ToolUsageRecorder(), pod="p", today=lambda: next(days))
    pruned: list[str] = []
    store.prune = lambda: pruned.append("x")  # ty: ignore[invalid-assignment]

    store.flush()
    store.flush()
    store.flush()

    assert pruned == ["x", "x"]  # the 14th once, the 15th once


def test_a_worker_flushes_on_its_interval_and_once_more_when_stopped() -> None:
    flushes: list[str] = []

    class _Store:
        def flush(self) -> None:
            flushes.append("f")

    stop = threading.Event()
    t = threading.Thread(target=run_flusher, args=(_Store(), stop, 0.01))
    t.start()
    for _ in range(200):
        if len(flushes) >= 2:
            break
        threading.Event().wait(0.01)
    stop.set()
    t.join(2)
    before = len(flushes)

    assert before >= 3  # at least two on the interval, then one at the stop
    assert not t.is_alive()


def _app(rec: ToolUsageRecorder, **kw):  # noqa: ANN003, ANN202
    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        tool_usage=rec,
        **kw,
    )
    return app, spec


def test_the_api_writes_what_it_counted_when_it_stops() -> None:
    from tests.api._client import TestClient

    rec = ToolUsageRecorder()
    app, spec = _app(rec)
    with TestClient(app):
        _count(rec)
        assert _rows(spec) == []  # nothing written yet: the interval is minutes

    [row] = _rows(spec)
    assert row.calls == 1


def test_the_api_writes_on_its_interval_while_it_runs() -> None:
    from datetime import timedelta

    from tests.api._client import TestClient

    rec = ToolUsageRecorder()
    app, spec = _app(rec, tool_usage_flush_interval=timedelta(milliseconds=20))
    with TestClient(app):
        _count(rec)
        for _ in range(200):
            if _rows(spec):
                break
            threading.Event().wait(0.01)
        assert [r.calls for r in _rows(spec)] == [1]


def test_two_processes_on_one_host_write_different_rows(monkeypatch) -> None:
    """Rows are owned by their writer, unlocked (get → add → update). Outside
    k8s `HOSTNAME` is a shell variable, not in the environment, so an API and
    its workers on one VM would all write `~local` — and overwrite each other's
    counts (review r1). The writer's name must differ per process."""
    from workspace_app.api import tool_usage_store as tus

    monkeypatch.delenv("HOSTNAME", raising=False)
    monkeypatch.setattr("os.getpid", lambda: 101)
    api = tus.writer_name()
    monkeypatch.setattr("os.getpid", lambda: 202)
    worker = tus.writer_name()

    assert api != worker


def test_the_api_writes_under_this_processs_name(monkeypatch) -> None:
    from workspace_app.api import tool_usage_store as tus

    monkeypatch.setattr(tus, "writer_name", lambda: "host-7")

    app, _spec = _app(ToolUsageRecorder())

    assert app.state.tool_usage_store._pod == "host-7"  # noqa: SLF001


def test_a_row_another_process_pruned_first_is_skipped_not_an_error() -> None:
    """Every process prunes on its first write of a UTC day, with no lease
    (plan §7 A10): two can find the same expired rows, and the slower one's
    delete of an id already gone must not abandon the rest (review r2)."""
    spec = _spec()
    for pod in ("a", "b", "c"):
        rec = ToolUsageRecorder(day=lambda: "2026-07-01")
        _count(rec)
        ToolUsageStore(spec, rec, pod=pod, today=lambda: "2026-07-01").flush()
    store = ToolUsageStore(spec, ToolUsageRecorder(), pod="p", today=lambda: "2026-10-14")
    real = store._rm()  # noqa: SLF001

    class _Racing:
        """The other process deletes the first match between search and delete."""

        def search_resources(self, **kw: object) -> list:
            metas = list(real.search_resources(**kw))
            real.permanently_delete(metas[0].resource_id)
            return metas

        def permanently_delete(self, rid: str) -> None:
            real.permanently_delete(rid)

    store._rm = lambda: _Racing()  # ty: ignore[invalid-assignment]  # noqa: SLF001

    store.prune()

    assert _rows(spec) == []


def test_a_skill_and_a_tool_of_one_name_are_stored_and_read_apart() -> None:
    """plan §8.3: one table, a `kind` per row — the row id carries it, so a
    skill and a tool of one name never add into one row, and a report reads
    one kind."""
    from workspace_app.api.tool_usage_store import rows_since

    spec = _spec()
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    # One name AND one source: only the kind keeps the two rows apart.
    rec.record("x", "s", failed=True, ms=40, user="u", surface="chat", app="", item="")
    rec.record("x", "s", kind="skill", failed=False, ms=0, user="u", surface="c", app="", item="")
    ToolUsageStore(spec, rec, pod="p", today=lambda: "2026-10-14").flush()

    tools = rows_since(spec, "2026-10-01")
    skills = rows_since(spec, "2026-10-01", kind="skill")

    assert [(r.kind, r.tool, r.calls, r.fails) for r in tools] == [("tool", "x", 1, 1)]
    assert [(r.kind, r.tool, r.calls, r.fails) for r in skills] == [("skill", "x", 1, 0)]


def test_the_skill_hub_counts_under_this_processs_name(monkeypatch) -> None:
    """The skill hub's counter had the same unlocked, `HOSTNAME`-keyed rows
    (plan §8.3): it now writes under the same per-process name."""
    import tempfile

    from workspace_app.apps.skill_hub import SkillHubStore
    from workspace_app.apps.skill_hub_git import SkillHubRepos

    monkeypatch.setattr("workspace_app.apps.skill_hub_usage.writer_name", lambda: "vm-7")

    hub = SkillHubStore(make_spec(), SkillHubRepos(tempfile.mkdtemp()))

    assert hub.usage._pod == "vm-7"  # noqa: SLF001


def test_a_rows_package_is_stored() -> None:
    from workspace_app.api.tool_usage_store import rows_since

    spec = _spec()
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    rec.record(
        "acme__ls",
        "third_party",
        package="acme",
        failed=False,
        ms=1,
        user="u",
        surface="c",
        app="",
        item="",
    )
    ToolUsageStore(spec, rec, pod="p", today=lambda: "2026-10-14").flush()

    [row] = rows_since(spec, "2026-10-01")
    assert row.package == "acme"


def test_one_command_name_in_two_packages_is_two_stored_rows() -> None:
    """Two Apps can each pick a package with a command of one name (the
    collision check is per App): the same tool name, two packages. Their counts
    must not add into one row (review r3)."""
    from workspace_app.api.tool_usage_store import rows_since

    spec = _spec()
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    for package, n in (("pkgA", 1), ("pkgB", 2)):
        for _ in range(n):
            rec.record(
                "summarize",
                "first_party",
                package=package,
                failed=False,
                ms=1,
                user="u",
                surface="c",
                app="",
                item="",
            )
    ToolUsageStore(spec, rec, pod="p", today=lambda: "2026-10-14").flush()

    got = sorted((r.package, r.calls) for r in rows_since(spec, "2026-10-01"))
    assert got == [("pkgA", 1), ("pkgB", 2)]


def test_a_tilde_in_a_name_cannot_make_two_rows_one() -> None:
    """`~` joins the parts of a row id, and `quote` leaves it alone: a name or a
    package holding one could spell another row's id (review r4)."""
    from workspace_app.api.tool_usage_store import _row_id

    assert _row_id("tool", "a~S", "S", "", "d", "p") != _row_id("tool", "a", "S", "S~", "d", "p")
