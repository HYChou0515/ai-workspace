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


class _FakeJudge(ILlm):
    """A judge ``ILlm`` that records the prompts it sees and yields a canned
    verdict text (the coordinator parses it for pass/fail + note)."""

    def __init__(self, text: str = '{"grade": "pass", "note": "ok"}') -> None:
        self.calls: list[str] = []
        self._text = text

    def set_reply(self, text: str) -> None:
        self._text = text

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.calls.append(prompt)
        yield self._text, False


def _q(pred):
    return next(q for q in QUESTIONS if pred(q))


def _cells_for(spec, model) -> set[tuple[str, str]]:
    rm = spec.get_resource_manager(SanityResult)
    return {
        (r.data.question_key, r.data.level)
        for r in rm.list_resources((QB["model"] == model).build())
        if isinstance(r.data, SanityResult)
    }


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


async def test_judge_grades_cell_when_wired():
    """#231 P2: when a judge ILlm is wired, each cell is also graded by the AI —
    ai_grade/ai_note land alongside the mechanical grade. The judge sees the
    question, the expected answer, and the model's output."""
    spec = make_spec(default_user="u")
    judge = _FakeJudge('{"grade": "fail", "note": "答錯了,首都是台北"}')
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="高雄"), judge=judge)
    taipei = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(taipei), "none")
    await coord.aclose()

    r = _result(spec, _MODEL, taipei, "none")
    assert r.grade == "fail"  # mechanical: 高雄 doesn't contain 台北
    assert r.ai_grade == "fail"  # AI judge agreed
    assert r.ai_note == "答錯了,首都是台北"
    assert taipei.expected in judge.calls[0] and "高雄" in judge.calls[0]


async def test_no_judge_leaves_ai_columns_empty():
    """#231 P2: judge unconfigured (None) ⇒ AI scoring gracefully off."""
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="台北"))
    q = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(q), "none")
    await coord.aclose()
    r = _result(spec, _MODEL, q, "none")
    assert r.ai_grade == "" and r.ai_note == ""


async def test_judge_not_called_on_run_error():
    """A failed run has no output to judge — the judge stays untouched."""
    spec = make_spec(default_user="u")
    judge = _FakeJudge()
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(fail=True), judge=judge)
    q = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(q), "none")
    await coord.aclose()
    r = _result(spec, _MODEL, q, "none")
    assert r.error and r.ai_grade == "" and r.ai_note == ""
    assert judge.calls == []


async def test_judge_parse_is_lenient_and_swallows_errors():
    """#231 P2: small local judges are messy — a bare 'fail' verdict (no JSON)
    still yields a grade; a judge that raises must not wreck the cell."""
    spec = make_spec(default_user="u")
    judge = _FakeJudge("這題我覺得 FAIL,因為答非所問")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="台北"), judge=judge)
    q = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(q), "none")
    await coord.aclose()
    r = _result(spec, _MODEL, q, "none")
    assert r.ai_grade == "fail"  # scanned out of prose
    assert r.grade == "pass"  # mechanical unaffected

    class _BoomJudge(ILlm):
        def stream(self, prompt: str):
            raise RuntimeError("judge down")
            yield  # pragma: no cover

    spec2 = make_spec(default_user="u")
    coord2 = SanityBatteryCoordinator(spec2, _FakeLlmFactory(output="台北"), judge=_BoomJudge())
    coord2.run_cell(_MODEL, question_key(q), "none")
    await coord2.aclose()  # must not raise
    r2 = _result(spec2, _MODEL, q, "none")
    assert r2.ai_grade == "" and r2.output == "台北"  # cell survives a judge crash


async def test_generate_verdict_writes_per_model_score_and_summary():
    """#231 P3: after a model's cells run, the judge reads them all and writes a
    per-model fitness verdict (0–100 + markdown summary)."""
    from workspace_app.resources import SanityVerdict, sanity_verdict_id

    spec = make_spec(default_user="u")
    judge = _FakeJudge('{"score": 82, "summary": "- KB 問答 OK\\n- JSON 格式強"}')
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="台北市"), judge=judge)
    q = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(q), "none")
    coord.wait_idle()
    coord.generate_verdict(_MODEL)
    await coord.aclose()

    rm = spec.get_resource_manager(SanityVerdict)
    v = rm.get(sanity_verdict_id(_MODEL)).data
    assert isinstance(v, SanityVerdict)
    assert v.model == _MODEL and v.score == 82 and "JSON" in v.summary
    # the verdict judge saw a digest of the model's cells (the output appears)
    assert any("台北市" in call for call in judge.calls)

    # re-generating overwrites the same verdict row (current-only)
    judge.set_reply('{"score": 50, "summary": "退步了"}')
    coord.generate_verdict(_MODEL)
    await coord.aclose()
    v2 = rm.get(sanity_verdict_id(_MODEL)).data
    assert isinstance(v2, SanityVerdict) and v2.score == 50
    assert rm.count_resources(QB.all().build()) == 1


async def test_generate_verdict_is_noop_without_judge_or_cells():
    """No judge ⇒ nothing written; a model with no cells ⇒ nothing to judge."""
    from workspace_app.resources import SanityVerdict

    spec = make_spec(default_user="u")
    no_judge = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    no_judge.run_cell(_MODEL, question_key(QUESTIONS[0]), "none")
    no_judge.wait_idle()
    no_judge.generate_verdict(_MODEL)  # judge is None → skip
    await no_judge.aclose()
    assert spec.get_resource_manager(SanityVerdict).count_resources(QB.all().build()) == 0

    spec2 = make_spec(default_user="u")
    with_judge = SanityBatteryCoordinator(spec2, _FakeLlmFactory(), judge=_FakeJudge())
    with_judge.generate_verdict("never-run-model")  # no cells → skip
    await with_judge.aclose()
    assert spec2.get_resource_manager(SanityVerdict).count_resources(QB.all().build()) == 0


async def test_generate_verdict_skips_when_judge_returns_nothing():
    """A judge that yields an unusable (empty) verdict must not write a misleading
    score — the cell ran, but no verdict row appears."""
    from workspace_app.resources import SanityVerdict

    spec = make_spec(default_user="u")
    judge = _FakeJudge("")  # empty reply → judge_verdict returns (0, "")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="台北"), judge=judge)
    coord.run_cell(_MODEL, question_key(QUESTIONS[0]), "none")
    coord.wait_idle()
    coord.generate_verdict(_MODEL)
    await coord.aclose()
    assert spec.get_resource_manager(SanityVerdict).count_resources(QB.all().build()) == 0


async def test_run_missing_enqueues_only_uncovered_cells():
    """#231 P4: run_missing fills exactly the never-run coverage cells (every
    question × its coverage_levels), skipping ones that already have a result."""
    from workspace_app.health.sanity.questions import coverage_levels

    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    want = {(question_key(q), lvl) for q in QUESTIONS for lvl in coverage_levels(q)}

    # seed one cell, then fill the rest
    first = next(iter(want))
    coord.run_cell(_MODEL, first[0], first[1])
    coord.wait_idle()
    queued = coord.run_missing(_MODEL)
    coord.wait_idle()
    await coord.aclose()

    assert queued == len(want) - 1  # all but the already-run cell
    assert _cells_for(spec, _MODEL) == want


async def test_run_missing_can_narrow_to_one_category():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    coord.run_missing(_MODEL, category="格式輸出")
    coord.wait_idle()
    await coord.aclose()
    got = _cells_for(spec, _MODEL)
    fmt_keys = {question_key(q) for q in QUESTIONS if q.category == "格式輸出"}
    assert got and {k for k, _ in got} == fmt_keys  # only that 題組 ran


async def test_rescore_rejudges_stored_output_without_rerunning_the_model():
    """#231 P4: rescore re-judges existing cells against their STORED output (the
    model is NOT called again) and refreshes the verdict."""
    spec = make_spec(default_user="u")
    factory = _FakeLlmFactory(output="台北")
    coord = SanityBatteryCoordinator(spec, factory, judge=_FakeJudge('{"grade":"pass","note":"a"}'))
    q = QUESTIONS[0]
    coord.run_cell(_MODEL, question_key(q), "none")
    coord.wait_idle()
    runs_after_first = len(factory.calls)

    # swap the judge's verdict, then rescore — no new model call, ai_grade updates
    coord._judge = _FakeJudge('{"grade":"fail","note":"重新判為錯"}')
    n = coord.rescore(_MODEL)
    await coord.aclose()

    assert n == 1
    assert len(factory.calls) == runs_after_first  # model NOT re-run
    r = _result(spec, _MODEL, q, "none")
    assert r.output == "台北" and r.ai_grade == "fail" and r.ai_note == "重新判為錯"


async def test_rescore_without_judge_is_a_noop():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    coord.run_cell(_MODEL, question_key(QUESTIONS[0]), "none")
    coord.wait_idle()
    assert coord.rescore(_MODEL) == 0
    await coord.aclose()


async def test_rescore_skips_errored_cells():
    spec = make_spec(default_user="u")
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(fail=True), judge=_FakeJudge())
    coord.run_cell(_MODEL, question_key(QUESTIONS[0]), "none")
    coord.wait_idle()
    assert coord.rescore(_MODEL) == 0  # the only cell errored → nothing to judge
    await coord.aclose()


def _add_custom(
    spec,
    *,
    category: str = "自訂",
    prompt: str = "2+2 等於多少?",
    expected: str = "4",
    levels: list[str] | None = None,
    enabled: bool = True,
):
    from workspace_app.resources import CustomSanityQuestion

    rm = spec.get_resource_manager(CustomSanityQuestion)
    rm.create(
        CustomSanityQuestion(
            category=category,
            prompt=prompt,
            expected=expected,
            levels=levels or ["none"],
            enabled=enabled,
        )
    )


async def test_custom_question_runs_ai_only_graded():
    """#231 P5: a user-authored question joins the matrix and is AI-only graded
    (no mechanical grader) — grade stays empty, ai_grade comes from the judge."""
    spec = make_spec(default_user="u")
    _add_custom(spec, prompt="台灣最高的山是?", expected="玉山")
    coord = SanityBatteryCoordinator(
        spec, _FakeLlmFactory(output="玉山"), judge=_FakeJudge('{"grade":"pass","note":"對"}')
    )
    cq = next(q for q in coord.all_questions() if q.category == "自訂")
    coord.run_cell(_MODEL, question_key(cq), "none")
    coord.wait_idle()
    await coord.aclose()

    r = _result(spec, _MODEL, cq, "none")
    assert r.grade == ""  # no mechanical grader for custom questions
    assert r.ai_grade == "pass" and r.output == "玉山"


async def test_disabled_custom_question_is_hidden_and_invalid_levels_dropped():
    spec = make_spec(default_user="u")
    _add_custom(spec, category="啟用", levels=["none", "bogus"])  # bogus level dropped
    _add_custom(spec, category="停用", enabled=False)  # hidden
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    cats = {q.category for q in coord.all_questions()}
    assert "啟用" in cats and "停用" not in cats
    enabled_q = next(q for q in coord.all_questions() if q.category == "啟用")
    assert enabled_q.auto_levels == ("none",)  # only the valid level survived


async def test_run_missing_covers_custom_questions():
    spec = make_spec(default_user="u")
    _add_custom(spec, category="自訂", levels=["none", "medium"])
    coord = SanityBatteryCoordinator(spec, _FakeLlmFactory(output="x"))
    cq = next(q for q in coord.all_questions() if q.category == "自訂")
    coord.run_missing(_MODEL, category="自訂")
    coord.wait_idle()
    await coord.aclose()
    got = _cells_for(spec, _MODEL)
    assert got == {(question_key(cq), "none"), (question_key(cq), "medium")}


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
