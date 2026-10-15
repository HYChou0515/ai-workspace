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
