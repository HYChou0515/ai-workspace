"""The non-idempotent capability shell (#435, decision 1/6) — the framework-locked
idempotency 外殼: a capability runs as TWO journaled steps (decide → act), verdict
hash-chained into act, so the three-state re-run (both cached / verdict cached + act
re-runs / neither) falls out of ``run_step``'s existing skip used twice. The owner
supplies only the ``decide``/``act`` bodies; the shell owns the journaling.
"""

from __future__ import annotations

from typing import Any

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import DslError, _lookup_step
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.nonidempotent import Result, Verdict, run_nonidempotent


def _wf() -> WorkflowHandle:
    return WorkflowHandle(
        store=MemoryFileStore(), workspace_id="ws", workflow_id="pm", user="alice"
    )


async def test_decide_and_act_run_once_then_both_skip_on_replay() -> None:
    """First run: decide once + act once, publishing act's fields at the author path.
    A replay with identical inputs skips BOTH (no re-decide, no re-act) — the journal's
    same-args skip used twice."""
    wf = _wf()
    calls = {"decide": 0, "act": 0}

    async def decide(_feedback: str | None) -> Verdict:
        calls["decide"] += 1
        return Verdict(kind="new")

    async def act(verdict: Verdict) -> Result:
        calls["act"] += 1
        assert verdict.kind == "new"
        return Result(fields={"number": 7})

    r1 = await run_nonidempotent(
        wf, name="create_thing", inputs={"title": "A"}, decide=decide, act=act
    )
    assert r1.fields == {"number": 7}
    assert calls == {"decide": 1, "act": 1}
    # two journaled records under one step folder: a private decide side-car + the
    # author-visible published result.
    assert await wf.exists("/.workflow/pm/step_create_thing/main.decide.json")
    assert await wf.exists("/.workflow/pm/step_create_thing/main.json")

    r2 = await run_nonidempotent(
        wf, name="create_thing", inputs={"title": "A"}, decide=decide, act=act
    )
    assert r2.fields == {"number": 7}
    assert calls == {"decide": 1, "act": 1}  # both skipped — cached identical inputs


async def test_act_crash_retry_reuses_the_verdict_without_re_deciding() -> None:
    """If act's side effect ran but its journal write was lost (simulated by deleting
    act.json), the replay skips decide (verdict cached) and re-runs ONLY act — the
    act-crash-retry path the two-step shell exists for (§7)."""
    wf = _wf()
    calls = {"decide": 0, "act": 0}

    async def decide(_feedback: str | None) -> Verdict:
        calls["decide"] += 1
        return Verdict(kind="new", payload={"seq": calls["decide"]})

    async def act(_verdict: Verdict) -> Result:
        calls["act"] += 1
        return Result(fields={"n": calls["act"]})

    await run_nonidempotent(wf, name="cap", inputs={"x": 1}, decide=decide, act=act)
    assert calls == {"decide": 1, "act": 1}

    # simulate act's result never landing (crash between side effect and journal write)
    await wf.delete("/.workflow/pm/step_cap/main.json")

    await run_nonidempotent(wf, name="cap", inputs={"x": 1}, decide=decide, act=act)
    assert calls == {"decide": 1, "act": 2}  # decide REUSED (still 1), act re-ran


async def test_changed_inputs_re_decide_and_re_act_via_hash_chaining() -> None:
    """Different inputs → decide re-runs → a different verdict → act's input-hash
    (which folds in the verdict) changes → act re-runs (§9 hash-chaining). This is how
    a gate revise that changes the capability's inputs re-drives decide+act."""
    wf = _wf()
    seen: list[dict[str, Any]] = []

    async def act(verdict: Verdict) -> Result:
        seen.append(dict(verdict.payload))
        return Result(fields={"ok": True})

    await run_nonidempotent(
        wf, name="cap", inputs={"title": "v1"}, decide=lambda _fb: _mk("new", {"of": "v1"}), act=act
    )
    await run_nonidempotent(
        wf, name="cap", inputs={"title": "v2"}, decide=lambda _fb: _mk("new", {"of": "v2"}), act=act
    )
    assert seen == [{"of": "v1"}, {"of": "v2"}]  # act re-ran with the new verdict


async def test_published_result_is_referenceable_but_verdict_side_car_is_not() -> None:
    """act's ``Result.fields`` land at the capability's published path so
    ``{steps.<cap>.<field>}`` resolves (the unified reference model); the verdict is a
    private side-car, never in the author's reference namespace (§4)."""
    wf = _wf()

    async def decide(_feedback: str | None) -> Verdict:
        return Verdict(kind="duplicate", payload={"of": 3})  # private ruling

    async def act(_verdict: Verdict) -> Result:
        return Result(fields={"number": 3, "merged": True})

    await run_nonidempotent(wf, name="file_issue", inputs={"title": "A"}, decide=decide, act=act)

    ns = {"__key__": ""}
    assert await _lookup_step(["steps", "file_issue", "number"], ns, wf) == 3
    assert await _lookup_step(["steps", "file_issue", "merged"], ns, wf) is True
    # the verdict's private payload key is NOT a published field
    with pytest.raises(DslError):
        await _lookup_step(["steps", "file_issue", "of"], ns, wf)


async def _mk(kind: str, payload: dict[str, Any]) -> Verdict:
    return Verdict(kind=kind, payload=payload)
