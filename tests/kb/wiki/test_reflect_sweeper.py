"""#479 daily reflection: WikiReflectSweeper.tick() enqueues a ``reflect`` job for
every PROSE wiki Collection due for its server-local daily wall-clock time
(``reflect_daily`` = ``HH:MM``). Pure producer — the survey/plan/apply runs in the
enqueued job. ``now_ms`` is built from a *local* datetime so the gate's local-time
maths round-trips regardless of the machine's timezone; the once-a-day gate reads
``Collection.last_reflected_at``."""

from __future__ import annotations

from datetime import datetime

from workspace_app.kb.wiki.reflect_sweeper import WikiReflectSweeper, _last_reflected_ms
from workspace_app.resources import Collection, make_spec


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _prose(spec, *, last_reflected_at: str = "", use_wiki: bool = True, git_url=None) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(
            Collection(
                name="kb",
                use_wiki=use_wiki,
                git_url=git_url,
                last_reflected_at=last_reflected_at,
            )
        )
        .resource_id
    )


def _sweeper(spec, enqueue, *, reflect_daily: str | None = "04:00") -> WikiReflectSweeper:
    return WikiReflectSweeper(spec, enqueue=enqueue, reflect_daily=reflect_daily)


def test_last_reflected_ms_none_when_never_reflected():
    assert _last_reflected_ms("") is None
    dt = datetime(2026, 1, 2, 4, 0)
    assert _last_reflected_ms(dt.isoformat()) == _ms(dt)


def test_tick_enqueues_a_prose_wiki_due_today():
    spec = make_spec(default_user="u")
    cid = _prose(spec)
    enq: list[str] = []
    assert _sweeper(spec, enq.append).tick(now_ms=_ms(datetime(2026, 1, 2, 4, 5))) == [cid]
    assert enq == [cid]


def test_tick_skips_before_the_daily_time():
    spec = make_spec(default_user="u")
    _prose(spec)
    enq: list[str] = []
    assert _sweeper(spec, enq.append).tick(now_ms=_ms(datetime(2026, 1, 2, 1, 0))) == []


def test_tick_reflects_again_the_next_day():
    spec = make_spec(default_user="u")
    cid = _prose(spec, last_reflected_at=datetime(2026, 1, 1, 4, 0).isoformat())
    enq: list[str] = []
    assert _sweeper(spec, enq.append).tick(now_ms=_ms(datetime(2026, 1, 2, 4, 5))) == [cid]


def test_tick_not_due_when_already_reflected_today():
    spec = make_spec(default_user="u")
    _prose(spec, last_reflected_at=datetime(2026, 1, 2, 4, 0).isoformat())
    enq: list[str] = []
    assert _sweeper(spec, enq.append).tick(now_ms=_ms(datetime(2026, 1, 2, 5, 0))) == []


def test_tick_off_when_reflect_daily_unset():
    spec = make_spec(default_user="u")
    _prose(spec)
    enq: list[str] = []
    s = _sweeper(spec, enq.append, reflect_daily=None)
    assert s.tick(now_ms=_ms(datetime(2026, 1, 2, 12, 0))) == []


def test_tick_skips_code_and_non_wiki_collections():
    spec = make_spec(default_user="u")
    _prose(spec, git_url="https://git.example/r.git")  # a code collection
    _prose(spec, use_wiki=False)  # wiki path off
    enq: list[str] = []
    assert _sweeper(spec, enq.append).tick(now_ms=_ms(datetime(2026, 1, 2, 5, 0))) == []


def test_tick_uses_wall_clock_when_now_ms_omitted():
    # no collections → the loop body never runs, so the result is [] regardless of
    # the real time — but the default `now_ms = time.time()` branch is exercised.
    spec = make_spec(default_user="u")
    assert WikiReflectSweeper(spec, enqueue=lambda _c: None, reflect_daily="04:00").tick() == []
