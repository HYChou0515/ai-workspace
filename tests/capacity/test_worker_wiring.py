"""A worker process counts its jobs as a worker (docs/plan-capacity-usage.md
§6.4, §6.7), and its last write comes after the drain — a job finishing while
the coordinator drains is counted, not lost."""

from __future__ import annotations

import threading

from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.sink import UsageSink
from workspace_app.worker.__main__ import serve

from .test_wiring import _settings


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def test_a_bundle_worker_records_as_a_worker(tmp_path):
    import workspace_app.worker.__main__ as wm

    worker = wm.build_worker(_settings(tmp_path), "wiki", config_dir=None)

    assert worker.usage_recorder is not None
    assert worker.usage_recorder.component == "worker"


def test_a_worker_built_from_the_api_composition_still_records_as_a_worker(tmp_path, monkeypatch):
    import workspace_app.worker.__main__ as wm

    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})

    worker = wm.build_worker(_settings(tmp_path), "chat-video", config_dir=None)

    assert worker.usage_recorder is not None
    assert worker.usage_recorder.component == "worker"


def test_a_worker_writes_what_finishes_while_draining():
    sink = _ListSink()
    rec = UsageRecorder(sink, component="worker", pod="w", window_s=300, queue_max=100)

    class _Coordinator:
        def start_consuming(self) -> None:
            pass

        async def aclose(self) -> None:
            import asyncio

            await asyncio.sleep(0.2)  # a job finishing in the drain
            rec.add("alice", "", "job:index", seconds=0.2)

    stop = threading.Event()
    stop.set()

    serve(_Coordinator(), None, stop, interval=3600, usage_recorder=rec)

    rows = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "work"]
    assert [(r["user"], r["work"]) for r in rows] == [("alice", "job:index")]


def test_the_worker_process_serves_the_recorder_it_built(tmp_path, monkeypatch):
    import workspace_app.worker.__main__ as wm

    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "workspace_app.config.loader.load_with_provenance", lambda **_kw: (settings, None)
    )
    monkeypatch.setattr("workspace_app.observability.setup.install_llm_logging", lambda _s: None)
    recorder = object()
    monkeypatch.setattr(
        wm,
        "build_worker",
        lambda *_a, **_kw: wm.Worker(object(), None, usage_recorder=recorder),  # ty: ignore[invalid-argument-type]
    )
    served: list = []
    monkeypatch.setattr(wm, "serve", lambda *a, **kw: served.append(kw.get("usage_recorder")))
    monkeypatch.setattr("signal.signal", lambda *_a: None)

    wm.main(["wiki"])

    assert served == [recorder]
