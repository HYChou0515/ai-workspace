"""The composition roots hand the runner and the writer ONE recorder
(docs/plan-tool-usage-stats.md §3.5) — two recorders would count every call
into one and write the other, which is empty: a report that reads zero."""

from __future__ import annotations

from textwrap import dedent

from workspace_app.config.loader import load


def _settings(tmp_path):  # noqa: ANN001, ANN202
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            server:
              run_consumers: false
            filestore:
              kind: specstar
            sandbox:
              root: {tmp_path / "sandbox"}
            skill_hub:
              git_root: {tmp_path / "skill-hub-git"}
            kb:
              embedder:
                model: ""
        """),
        encoding="utf-8",
    )
    return load(config_path=cfg, env={})


def test_the_api_counts_into_the_recorder_it_writes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from workspace_app.__main__ import build_app

    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    app = build_app(_settings(tmp_path), config_dir=None)

    store = app.state.tool_usage_store
    runner = app.state.turn_engine._runner  # noqa: SLF001
    assert store is not None
    assert runner._tool_usage is store._recorder  # noqa: SLF001


def test_a_worker_counts_into_the_recorder_it_writes(tmp_path) -> None:  # noqa: ANN001
    from workspace_app.agent.tool_usage import ToolUsageRecorder
    from workspace_app.factories import get_runner

    rec = ToolUsageRecorder()

    runner = get_runner(_settings(tmp_path), tool_usage=rec)

    assert runner._tool_usage is rec  # ty: ignore[unresolved-attribute]  # noqa: SLF001


def test_a_bundle_worker_writes_what_its_agents_count_through_the_spec_they_use(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """A wiki / card-gen worker: the recorder `build_bundle` hands its agents is
    the one written, through the bundle's own spec — not a second backend
    connection opened just to write the counts (review r1)."""
    import workspace_app.worker.__main__ as wm

    seen: dict = {}
    real = wm.build_bundle

    def spy(settings, spec, **kw):  # noqa: ANN001, ANN003, ANN202
        seen["spec"], seen["rec"] = spec, kw["tool_usage"]
        return real(settings, spec, **kw)

    monkeypatch.setattr(wm, "build_bundle", spy)

    worker = wm.build_worker(_settings(tmp_path), "wiki", config_dir=None)

    store = worker.usage_store
    assert store is not None
    assert store._recorder is seen["rec"]  # noqa: SLF001
    assert store._spec is seen["spec"]  # noqa: SLF001


def test_an_api_composed_worker_writes_the_apps_own_counts(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """blob-gc / chat-video are composed by `build_app`, whose runner counts into
    the APP's recorder — a separate recorder in the worker would be flushed
    empty while the app's is never written (review r1)."""
    import workspace_app.__main__ as main_mod
    import workspace_app.worker.__main__ as wm

    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    built: list = []
    real = main_mod.build_app

    def spy(settings, **kw):  # noqa: ANN001, ANN003, ANN202
        built.append(real(settings, **kw))
        return built[-1]

    monkeypatch.setattr(main_mod, "build_app", spy)

    worker = wm.build_worker(_settings(tmp_path), "chat-video", config_dir=None)

    assert worker.usage_store is built[0].state.tool_usage_store


def test_a_worker_writes_what_its_jobs_count_while_draining() -> None:
    """SIGTERM sets `stop`; the coordinator then drains, and a job finishing in
    that drain counts tool calls. The last write must come AFTER the drain —
    one triggered by the same `stop` runs before it and loses them (review r1)."""
    import threading

    from workspace_app.agent.tool_usage import ToolUsageRecorder
    from workspace_app.worker.__main__ import serve

    rec = ToolUsageRecorder()
    written: list[int] = []

    class _Store:
        def flush(self) -> None:
            written.append(len(rec.drain()))

    class _Coordinator:
        def start_consuming(self) -> None:
            pass

        async def aclose(self) -> None:
            import asyncio

            await asyncio.sleep(0.3)  # the drain: a job finishing late
            rec.record(
                "read_file",
                "builtin",
                surface="background",
                user="",
                app="",
                item="",
                ms=5,
                failed=False,
            )

    stop = threading.Event()
    stop.set()

    serve(_Coordinator(), _Store(), stop, interval=3600)

    assert sum(written) == 1


def test_the_worker_process_serves_the_writer_it_built(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import workspace_app.worker.__main__ as wm

    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "workspace_app.config.loader.load_with_provenance",
        lambda **_kw: (settings, None),
    )
    monkeypatch.setattr("workspace_app.observability.setup.install_llm_logging", lambda _s: None)
    store, coordinator = object(), object()
    monkeypatch.setattr(wm, "build_worker", lambda *_a, **_kw: wm.Worker(coordinator, store))  # ty: ignore[invalid-argument-type]
    served: list = []
    monkeypatch.setattr(
        wm, "serve", lambda c, s, stop, *, interval: served.append((c, s, interval))
    )
    monkeypatch.setattr(wm.signal, "signal", lambda *_a: None)

    wm.main(["wiki"])

    from workspace_app.api.tool_usage_store import FLUSH_INTERVAL_S

    assert served == [(coordinator, store, FLUSH_INTERVAL_S)]
