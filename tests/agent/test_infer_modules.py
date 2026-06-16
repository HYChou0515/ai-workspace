"""#66: infer_modules faithfully classifies EVERY process step.

The old tool batched the whole `step_name` list into one sub-agent call,
which overflowed / skipped steps at scale (~1500). The new tool takes a
FILE (a wafer-history CSV column, or a plain list), iterates each unique
step through its own classification call (parallel), and writes a
pandera-validated `module-map.csv`. A step that fails to classify is
written as `unknown` and listed in the summary — never aborts the run.
"""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import (
    _infer_modules_summary,
    _module_map_csv,
    _parse_module_json,
    _read_step_names,
    infer_modules_impl,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def test_read_step_names_from_csv_column_unique_in_order():
    text = "wafer,step_name,t\nW1,STI_grow,1\nW1,Gate_pvd,2\nW2,STI_grow,3\n"
    assert _read_step_names(text, "step_name") == ["STI_grow", "Gate_pvd"]


def test_read_step_names_from_plain_line_list_when_column_absent():
    text = "STI_grow\nGate_pvd\nSTI_grow\n\n"
    assert _read_step_names(text, "step_name") == ["STI_grow", "Gate_pvd"]


def test_read_step_names_empty_text_yields_nothing():
    assert _read_step_names("   \n  ", "step_name") == []


def test_parse_module_json_extracts_module_and_reason():
    answer = 'Sure.\n{"module": "STI", "reason": "prefix STI_"}\n\nSources: [1] x.md'
    assert _parse_module_json(answer) == ("STI", "prefix STI_")


def test_parse_module_json_tolerates_code_fences():
    answer = '```json\n{"module": "M4", "reason": "M4_ prefix"}\n```'
    assert _parse_module_json(answer) == ("M4", "M4_ prefix")


def test_parse_module_json_returns_unknown_on_garbage():
    assert _parse_module_json("I could not decide.")[0] == "unknown"


def test_parse_module_json_returns_unknown_on_empty_module():
    assert _parse_module_json('{"module": "", "reason": "n/a"}')[0] == "unknown"


def test_module_map_csv_is_pandera_validated_and_has_header():
    out = _module_map_csv([("STI_grow", "STI", "by prefix"), ("X_etch", "unknown", "")])
    text = out.decode("utf-8")
    assert text.splitlines()[0] == "step_name,module,reason"
    assert "STI_grow,STI,by prefix" in text


def test_infer_modules_summary_is_json_topk_with_other_unknown_breakdown():
    """The LLM-facing summary is a JSON object: top-5 real modules by count,
    totals, and Other / Unknown counts — never the per-step names."""
    rows = (
        [("s", "STI", "")] * 10
        + [("s", "Gate", "")] * 8
        + [("s", "M1", "")] * 6
        + [("s", "M2", "")] * 4
        + [("s", "M3", "")] * 3
        + [("s", "M4", "")] * 2  # 6th real kind — must fall outside the top 5
        + [("s", "Other", "")] * 5
        + [("s", "unknown", "")] * 7
    )
    summary = json.loads(_infer_modules_summary(rows, "step2-data/module-map.csv"))
    assert list(summary["counts_topk"]) == ["STI", "Gate", "M1", "M2", "M3"]  # top 5, high→low
    assert "M4" not in summary["counts_topk"]
    assert summary["total_counts"] == 45
    assert summary["total_kind"] == 6  # STI,Gate,M1,M2,M3,M4 (Other/Unknown excluded)
    assert summary["Others"] == 5
    assert summary["Unknown"] == 7
    assert summary["out"] == "step2-data/module-map.csv"


async def _ctx_with_file(content: bytes, run, *, parallelism: int = 4):
    fs = MemoryFileStore()
    files = WorkspaceFiles(fs)
    inv = "ws-1"
    await files.create(inv, "wafer-history.csv", content)
    ctx = AgentToolContext(
        filestore=fs,
        files=files,
        investigation_id=inv,
        run_subagent=run,
        infer_modules_parallelism=parallelism,
    )
    return ctx, files, inv


async def test_infer_modules_classifies_each_unique_step_and_writes_module_map():
    """One sub-agent call per UNIQUE step (not per row), aggregated into a
    written module-map.csv; returns a summary, not the full map."""
    calls: list[str] = []

    async def fake_run(purpose, payload, sink, origin):
        assert purpose == "infer_modules"
        step = json.loads(payload)["step_name"]
        calls.append(step)
        module = "STI" if step.startswith("STI") else "Gate"
        return f'{{"module": "{module}", "reason": "by prefix"}}', []

    ctx, files, inv = await _ctx_with_file(
        b"wafer,step_name\nW1,STI_grow\nW1,Gate_pvd\nW2,STI_grow\n", fake_run
    )
    result = await infer_modules_impl(RunContextWrapper(ctx), "wafer-history.csv")

    assert sorted(calls) == ["Gate_pvd", "STI_grow"]  # unique, each once
    written = (await files.read(inv, "step2-data/module-map.csv")).decode("utf-8")
    assert "step_name,module,reason" in written
    assert "STI_grow,STI,by prefix" in written
    assert "Gate_pvd,Gate,by prefix" in written
    summary = json.loads(result)
    assert summary["total_counts"] == 2
    assert summary["total_kind"] == 2
    assert summary["counts_topk"] == {"STI": 1, "Gate": 1}
    assert summary["Unknown"] == 0
    assert summary["out"] == "step2-data/module-map.csv"


async def test_infer_modules_writes_unknown_for_failed_steps_without_aborting():
    """A step whose sub-agent errors (or returns garbage) is written as
    `unknown` and listed in the summary; the rest still classify."""

    async def fake_run(purpose, payload, sink, origin):
        step = json.loads(payload)["step_name"]
        if step == "Boom_step":
            raise RuntimeError("kb down")
        return '{"module": "STI", "reason": "ok"}', []

    ctx, files, inv = await _ctx_with_file(b"step_name\nSTI_grow\nBoom_step\n", fake_run)
    result = await infer_modules_impl(RunContextWrapper(ctx), "wafer-history.csv")

    written = (await files.read(inv, "step2-data/module-map.csv")).decode("utf-8")
    assert "STI_grow,STI,ok" in written
    assert "Boom_step,unknown," in written  # the failed name lives in the CSV
    summary = json.loads(result)
    assert summary["Unknown"] == 1  # the summary reports the COUNT, not the name
    assert "Boom_step" not in result  # never the per-step names in the summary


async def test_infer_modules_overwrites_an_existing_map_on_rerun():
    async def fake_run(purpose, payload, sink, origin):
        return '{"module": "STI", "reason": "ok"}', []

    ctx, files, inv = await _ctx_with_file(b"step_name\nSTI_grow\n", fake_run)
    await files.create(inv, "step2-data/module-map.csv", b"stale\n")  # pre-existing
    await infer_modules_impl(RunContextWrapper(ctx), "wafer-history.csv")
    written = (await files.read(inv, "step2-data/module-map.csv")).decode("utf-8")
    assert "stale" not in written
    assert "STI_grow,STI,ok" in written


async def test_infer_modules_classifies_steps_in_parallel_bounded_by_parallelism():
    """The per-step classification fans out concurrently, bounded by the
    configured parallelism — NOT one step at a time. Measured via the peak
    number of sub-agent calls in flight at once."""
    import asyncio

    in_flight = 0
    peak = 0

    async def fake_run(purpose, payload, sink, origin):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)  # hold the slot so overlap is observable
        in_flight -= 1
        return '{"module": "STI", "reason": "x"}', []

    steps = b"step_name\n" + b"\n".join(f"S{i}".encode() for i in range(8))
    ctx, _files, _inv = await _ctx_with_file(steps, fake_run, parallelism=4)
    await infer_modules_impl(RunContextWrapper(ctx), "wafer-history.csv")

    assert peak > 1, "steps ran sequentially — not parallel"
    assert peak <= 4, "exceeded the configured parallelism cap"


async def test_infer_modules_errors_on_missing_file():
    async def fake_run(purpose, payload, sink, origin):  # pragma: no cover — never called
        return "{}", []

    ctx, _files, _inv = await _ctx_with_file(b"step_name\nX\n", fake_run)
    result = await infer_modules_impl(RunContextWrapper(ctx), "does-not-exist.csv")
    assert "file not found" in result
