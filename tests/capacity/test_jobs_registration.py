"""Every job type is on the usage record (docs/plan-capacity-usage.md §6.4).

A coordinator that registers its handler without `recorded_job` is a job type
missing from every view and every attribution — silently, since nothing else
reads the record. So: every `job_handler=` in `src/` is a `recorded_job(...)`
call whose label is a JobType the worker knows (`worker._JOBTYPE_ATTR`, the
list `python -m workspace_app.worker <jobtype>` accepts — not derived from the
lines checked here), every JobType is covered, and the recorder passed is the
coordinator's own `self._usage_recorder`, which `build_coordinators` sets
(`test_every_coordinator_built_holds_the_recorder`).
"""

from __future__ import annotations

import ast
from pathlib import Path

from workspace_app.worker import _JOBTYPE_ATTR

SRC = Path(__file__).resolve().parents[2] / "src" / "workspace_app"


def _registrations() -> list[tuple[str, ast.expr]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "job_handler":
                found.append((f"{path.relative_to(SRC)}:{node.value.lineno}", node.value))
    return found


def _label(call: ast.expr) -> str | None:
    """The JobType a `recorded_job("<jobtype>", self._handle, self._usage_recorder)` call names."""
    if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "recorded_job"):
        return None
    if len(call.args) != 3 or not isinstance(call.args[0], ast.Constant):
        return None
    recorder = call.args[2]
    if not (
        isinstance(recorder, ast.Attribute)
        and recorder.attr == "_usage_recorder"
        and getattr(recorder.value, "id", None) == "self"
    ):
        return None
    label = call.args[0].value
    return label if isinstance(label, str) else None


def test_every_job_handler_is_registered_through_recorded_job():
    registrations = _registrations()
    assert registrations, "no job_handler= found — the scan is looking in the wrong place"

    unwrapped = [where for where, call in registrations if _label(call) is None]

    assert unwrapped == [], (
        f"registered without recorded_job(<jobtype>, handler, self._usage_recorder): {unwrapped}"
    )


def test_every_jobtype_the_worker_knows_is_recorded_under_its_own_name():
    labels = [_label(call) for _where, call in _registrations()]

    assert sorted(labels) == sorted(_JOBTYPE_ATTR)


class _ListSink:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def _recorder():
    from workspace_app.capacity.recorder import UsageRecorder
    from workspace_app.capacity.sink import UsageSink

    class _Sink(_ListSink, UsageSink):
        pass

    sink = _Sink()
    return sink, UsageRecorder(sink, component="worker", pod="w", window_s=300, queue_max=100)


class _Llm:
    """Never called: the coordinators that need a model are only built here."""


def test_every_coordinator_built_holds_the_recorder():
    from workspace_app.resources import make_spec

    from ..test_coordinators import _build, _FakeIngestor

    _sink, rec = _recorder()
    bundle = _build(
        make_spec(default_user="u"),
        _FakeIngestor(),
        usage_recorder=rec,
        sanity_llm_factory=lambda *_a, **_kw: _Llm(),
        eval_llm=_Llm(),
        graph_llm=_Llm(),
    )

    queued = {name: getattr(bundle, attr) for name, attr in _JOBTYPE_ATTR.items()}
    assert all(c is not None for c in queued.values()), "every JobType's coordinator was built"
    assert {name: c._usage_recorder is rec for name, c in queued.items()} == dict.fromkeys(  # noqa: SLF001
        queued, True
    )


async def test_an_index_job_through_the_real_queue_is_recorded():
    from workspace_app.resources import make_spec

    from ..test_coordinators import _build, _collection, _doc, _FakeIngestor

    sink, rec = _recorder()
    spec = make_spec(default_user="alice")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ingestor = _FakeIngestor()
    bundle = _build(spec, ingestor, usage_recorder=rec, get_user_id=lambda: "alice")

    bundle.index.enqueue(doc_id, cid)
    await bundle.index.aclose()
    rec.close()

    assert ingestor.indexed == [doc_id]
    jobs = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "job"]
    assert [(j["jobtype"], j["outcome"]) for j in jobs] == [("index", "done")]
    work = [d["usage"]["work"] for d in sink.docs if d["usage"]["kind"] == "work"]
    assert work == ["job:index"]
