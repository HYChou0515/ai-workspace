"""`python -m workspace_app.capacity check` (docs/plan-capacity-usage.md §8): the
operator implements the four sources, runs this, and learns what is wrong
before the dashboard silently draws it wrong."""

from __future__ import annotations

from workspace_app.capacity.check import check_sources, main
from workspace_app.capacity.sources import (
    CapacityEntry,
    CapacitySources,
    GpuMetricsSource,
    GpuSample,
    ResourceMetricsSource,
    ResourceSample,
)

from .world import World, WorldGpus, WorldLlm, WorldPods, WorldRecords


def _world() -> World:
    return World(days=2, users=6)


def _errors(findings) -> list[str]:  # noqa: ANN001
    return [f.message for f in findings if f.level == "error"]


def test_sources_that_keep_the_contract_have_no_errors():
    w = _world()
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))

    findings = check_sources(sources, now_ms=w.end_ms, hours=24, step_s=w.step_s)

    assert _errors(findings) == []
    assert any(f.source == "usage_records" and "rows" in f.message for f in findings)


def test_unaligned_buckets_unknown_components_and_impossible_values_are_errors():
    w = _world()

    class _Wrong(ResourceMetricsSource):
        def usage(self, start_ms, end_ms, step_s):
            return [
                ResourceSample(start_ms + 1234, "api", "api-1", cpu_cores=0.5),
                ResourceSample(start_ms, "apiserver", "api-2", cpu_cores=0.5),
                ResourceSample(start_ms, "api", "api-3", cpu_cores=64.0),
            ]

        def capacity(self):
            return [CapacityEntry("api", 2.0, None, None, 1, "pod")]

    class _BadGpu(GpuMetricsSource):
        def usage(self, start_ms, end_ms, step_s):
            return [GpuSample(start_ms, "gpu0", "qwen3", 87.0, 1.0)]

        def cards(self):
            return []

    findings = check_sources(
        CapacitySources(resource_metrics=_Wrong(), gpu_metrics=_BadGpu()),
        now_ms=w.end_ms,
        hours=24,
        step_s=w.step_s,
    )

    errors = " | ".join(_errors(findings))
    assert "not aligned" in errors
    assert "apiserver" in errors
    assert "more CPU than" in errors
    assert "util" in errors


def test_a_source_that_raises_is_reported_with_its_error():
    class _Down(ResourceMetricsSource):
        def usage(self, start_ms, end_ms, step_s):
            raise ConnectionError("prometheus unreachable")

        def capacity(self):
            return []

    w = _world()
    findings = check_sources(
        CapacitySources(resource_metrics=_Down()), now_ms=w.end_ms, hours=24, step_s=w.step_s
    )

    assert any("prometheus unreachable" in m for m in _errors(findings))
    assert any(f.source == "usage_records" and f.level == "info" for f in findings)


def test_the_command_exits_non_zero_on_an_error(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "capacity:\n  resource_metrics: tests.capacity.test_check.Unreachable\n", encoding="utf-8"
    )

    code = main(["check", "--config", str(cfg), "--hours", "1"])

    assert code == 1
    assert "prometheus unreachable" in capsys.readouterr().out


class Unreachable(ResourceMetricsSource):
    def usage(self, start_ms, end_ms, step_s):
        raise ConnectionError("prometheus unreachable")

    def capacity(self):
        return []


def _findings(sources, w: World):  # noqa: ANN001, ANN202
    return check_sources(sources, now_ms=w.end_ms, hours=24, step_s=w.step_s)


def _said(findings, source: str, level: str) -> str:  # noqa: ANN001
    return " | ".join(f.message for f in findings if f.source == source and f.level == level)


def test_an_hour_without_machine_samples_is_reported_as_a_gap():
    w = _world()
    hole = (w.end_ms - 6 * 3_600_000, w.end_ms - 5 * 3_600_000)

    class Holed(WorldPods):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [
                s
                for s in super().usage(start_ms, end_ms, step_s)
                if not hole[0] <= s.t_ms < hole[1]
            ]

    findings = _findings(CapacitySources(resource_metrics=Holed(w)), w)

    assert "gap" in _said(findings, "resource_metrics", "warning")


def test_card_and_llm_rows_off_the_slot_are_errors():
    from workspace_app.capacity.sources import LlmLatencyRow, LlmRequestSource, LlmUsageRow

    w = _world()

    class OffGpu(GpuMetricsSource):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [GpuSample(start_ms + 1234, "gpu0", "m", 0.5, 1.0)]

        def cards(self):  # noqa: ANN201
            return []

    class OffLlm(LlmRequestSource):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [LlmUsageRow(start_ms + 1234, "m", ("ws", "ws.user:u"), 1, 1, 1, 1.0)]

        def latency(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [LlmLatencyRow(start_ms + 1234, "m", 1, 0.1, 0.2, 30.0)]

    findings = _findings(CapacitySources(gpu_metrics=OffGpu(), llm_requests=OffLlm()), w)

    assert "not aligned" in _said(findings, "gpu_metrics", "error")
    assert "not aligned" in _said(findings, "llm_requests", "error")


def test_platform_tags_that_do_not_parse_are_reported():
    from workspace_app.capacity.sources import LlmRequestSource, LlmUsageRow

    w = _world()

    class Garbled(LlmRequestSource):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [LlmUsageRow(start_ms, "m", ("ws", "ws.user", "ws.colour:red"), 1, 1, 1, 1.0)]

        def latency(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return []

    findings = _findings(CapacitySources(llm_requests=Garbled()), w)

    assert "malformed" in _said(findings, "llm_requests", "warning")


def test_sandbox_rows_are_counted_and_a_window_that_cannot_fit_the_slot_is_an_error():
    """sandbox-host's window is its own setting: one that neither divides the
    slot nor is a whole number of slots cannot be placed in the analysis."""
    from workspace_app.capacity.sources import aggregate_documents
    from workspace_app.capacity.usage_wire import document

    w = _world()
    rows = [
        document(
            "sandbox",
            w.end_ms - k * 420_000,
            pod="sbh",
            component="sandbox-host",
            fields={"user": "u", "app": "", "cpu_s": 1.0, "window_s": 420},
        )
        for k in range(1, 4)
    ]

    class Odd(WorldRecords):
        def aggregate(self, q):  # noqa: ANN001, ANN201
            return aggregate_documents(rows, q) if q.kind == "sandbox" else super().aggregate(q)

    findings = _findings(CapacitySources(Odd(w)), w)

    assert "420" in _said(findings, "usage_records", "error")
    assert "3 sandbox rows" in _said(findings, "usage_records", "info")


def test_a_kernel_without_pressure_is_reported():
    w = World(days=2, users=6, psi=False)

    findings = _findings(CapacitySources(WorldRecords(w)), w)

    assert "PSI" in _said(findings, "usage_records", "warning")


def test_the_gap_count_is_every_gap_not_the_ones_listed():
    """Only the five longest gaps are listed; "5 gap(s)" for seven said two
    of them were not there."""
    w = _world()
    holes = [
        (w.end_ms - (2 * k + 1) * 3_600_000, w.end_ms - 2 * k * 3_600_000 - 1_800_000)
        for k in range(1, 8)
    ]

    class Holed(WorldPods):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [
                s
                for s in super().usage(start_ms, end_ms, step_s)
                if not any(a <= s.t_ms < b for a, b in holes)
            ]

    findings = _findings(CapacitySources(resource_metrics=Holed(w)), w)

    assert "7 gap(s)" in _said(findings, "resource_metrics", "warning")
