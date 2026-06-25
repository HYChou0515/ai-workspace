"""SanityBatteryCoordinator — enqueue → background-run → grade → upsert cell.

The coordinator runs cells through the SAME ``ILlm`` seam kb_search uses; tests
inject a fake ``ILlm`` factory (production wires ``LitellmLlm``).
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB

from workspace_app.health.sanity.coordinator import SanityBatteryCoordinator
from workspace_app.health.sanity.questions import (
    QUESTIONS,
    SanityQuestion,
    auto_run_cells,
    messages_to_prompt,
    question_key,
    user,
)
from workspace_app.kb.llm import ILlm
from workspace_app.resources import SanityResult, make_spec, sanity_result_id

_MODEL = "ollama_chat/qwen3:14b"


class _FakeLlmFactory:
    """A ``(model, level) -> ILlm`` factory that records its calls and yields a
    canned answer — reasoning when ``level != "none"`` (mimics think on/off)."""

    def __init__(self, output: str = "ok", *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []  # (model, level, prompt)
        self._output = output
        self._fail = fail

    def set_output(self, output: str) -> None:
        self._output = output

    def __call__(self, model: str, level: str) -> ILlm:
        outer = self

        class _Llm(ILlm):
            def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
                outer.calls.append((model, level, prompt))
                if outer._fail:
                    raise RuntimeError("model down")
                if level != "none":
                    yield "<think>…</think>", True  # reasoning chunk
                yield outer._output, False

        return _Llm()


def _q(pred):
    return next(q for q in QUESTIONS if pred(q))


def _result(spec, model, q, level) -> SanityResult:
    rid = sanity_result_id(model, question_key(q), level)
    data = spec.get_resource_manager(SanityResult).get(rid).data
    assert isinstance(data, SanityResult)  # narrow Struct | UnsetType for ty
    return data


async def test_run_cell_runs_grades_and_upserts():
    spec = make_spec(default_user="u")
    factory = _FakeLlmFactory(output="首都是台北市")
    coord = SanityBatteryCoordinator(spec, factory)
    taipei = QUESTIONS[0]  # contains-台北 grader

    coord.run_cell(_MODEL, question_key(taipei), "none")
    await coord.aclose()

    r = _result(spec, _MODEL, taipei, "none")
    assert r.output == "首都是台北市"
    assert r.grade == "pass"
    assert r.reasoned is False  # level none → no thinking
    assert r.error == ""
    # the cell ran through the ILlm seam with the flattened prompt
    assert factory.calls == [(_MODEL, "none", messages_to_prompt(taipei.messages))]


async def test_grade_fails_and_reasoned_tracks_level():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="高雄"))
    taipei = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(taipei), "medium")
    await coord.aclose()
    r = _result(spec, _MODEL, taipei, "medium")
    assert r.grade == "fail"  # "高雄" doesn't contain 台北
    assert r.reasoned is True  # level medium → thinking


async def test_multiturn_prompt_is_flattened_for_the_llm():
    spec = make_spec(default_user="u")
    factory = _FakeLlmFactory(output="咪咪")
    coord = SanityBatteryCoordinator(spec, factory)
    multi = _q(lambda q: q.category == "多輪對話")
    coord.run_cell(_MODEL, question_key(multi), "none")
    await coord.aclose()
    # both turns reach the model in one prompt (so context is testable)
    _, _, prompt = factory.calls[0]
    assert "咪咪" in prompt and "我的貓叫什麼名字" in prompt
    assert _result(spec, _MODEL, multi, "none").grade == "pass"


async def test_aux_is_filled_for_eyeball_questions():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="海洋很大"))
    essay = _q(lambda q: q.aux is not None)  # the 300-字 essay
    coord.run_cell(_MODEL, question_key(essay), "low")
    await coord.aclose()
    r = _result(spec, _MODEL, essay, "low")
    assert r.grade == "" and r.aux == "4 字"


async def test_llm_error_is_recorded_not_raised():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(fail=True))
    q = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(q), "none")
    await coord.aclose()  # must not raise
    r = _result(spec, _MODEL, q, "none")
    assert "model down" in r.error and r.output == "" and r.grade == ""


async def test_run_battery_fills_every_auto_cell():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    coord.run_battery(_MODEL)
    await coord.aclose()

    rm = spec.get_resource_manager(SanityResult)
    rows = [r.data for r in rm.list_resources((QB["model"] == _MODEL).build())]
    assert all(isinstance(r, SanityResult) for r in rows)  # narrow for ty
    want = {(question_key(q), lvl) for q, lvl in auto_run_cells()}
    got = {(r.question_key, r.level) for r in rows if isinstance(r, SanityResult)}
    assert got == want
    assert len(rows) == len(auto_run_cells())  # no dupes


async def test_battery_fans_out_one_cell_job_per_cell():
    """#227: a battery no longer runs every cell inline in one long handler (which
    could trip the broker's consumer-ack timeout) — it enqueues one short cell
    job per cell. Observable as a SanityRun per cell (plus the battery itself)."""
    from workspace_app.health.sanity.jobs import SanityRun

    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    coord.run_battery(_MODEL)
    await coord.aclose()

    n_cells = len(auto_run_cells())
    job_rm = spec.get_resource_manager(SanityRun)
    runs = [
        r.data for r in job_rm.list_resources(QB.all().build()) if isinstance(r.data, SanityRun)
    ]
    cell_jobs = [r for r in runs if r.payload.scope == "cell"]
    battery_jobs = [r for r in runs if r.payload.scope == "battery"]
    assert len(battery_jobs) == 1
    assert len(cell_jobs) == n_cells  # one job per cell, fanned out


async def test_cell_for_an_edited_away_question_is_a_noop():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory())
    coord.run_cell(_MODEL, "deadbeefdeadbeef", "none")  # no such question_key
    await coord.aclose()
    rm = spec.get_resource_manager(SanityResult)
    assert rm.count_resources((QB["model"] == _MODEL).build()) == 0


async def test_aclose_is_a_noop_when_idle_and_accepts_a_factory():
    from specstar.message_queue import SimpleMessageQueueFactory

    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(
        spec, _FakeLlmFactory(), message_queue_factory=SimpleMessageQueueFactory()
    )
    await coord.aclose()  # never enqueued/consumed → early return, no thread spun up


def _raise(_out: str):
    raise RuntimeError("grader bug")


def test_grade_and_aux_swallow_grader_exceptions():
    boom_grade = SanityQuestion("x", [user("hi")], "e", grade=_raise)
    assert SanityBatteryCoordinator._grade(boom_grade, "out") == ""
    boom_aux = SanityQuestion("x", [user("hi")], "e", aux=_raise)
    assert SanityBatteryCoordinator._aux(boom_aux, "out") == ""
    plain = SanityQuestion("x", [user("hi")], "e")
    assert SanityBatteryCoordinator._grade(plain, "o") == ""
    assert SanityBatteryCoordinator._aux(plain, "o") == ""


async def test_rerun_overwrites_the_same_cell():
    spec = make_spec(default_user="u")
    q = QUESTIONS[0]
    factory = _FakeLlmFactory(output="台北")
    coord = SanityBatteryCoordinator(spec, factory)
    coord.run_cell(_MODEL, question_key(q), "none")
    coord.wait_idle()
    factory.set_output("臺北市更新")
    coord.run_cell(_MODEL, question_key(q), "none")
    coord.wait_idle()
    await coord.aclose()
    assert _result(spec, _MODEL, q, "none").output == "臺北市更新"
    rm = spec.get_resource_manager(SanityResult)
    assert rm.count_resources((QB["model"] == _MODEL).build()) == 1  # still one row
