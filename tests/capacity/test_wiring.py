"""The composition roots record what `capacity:` says (docs/plan-capacity-usage.md
§6.7, §12): through `config.yaml` → `build_app`, the deploy's own composition
root, a request on the real app becomes a JSON line on stdout."""

from __future__ import annotations

import json
from textwrap import dedent
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from workspace_app.capacity.sink import UsageSink
from workspace_app.config.loader import load


class CapturingSink(UsageSink):
    """Named by dotted path in a test config; keeps what it was given."""

    docs: ClassVar[list[dict]] = []

    def write(self, documents):
        CapturingSink.docs.extend(documents)


def _settings(tmp_path, capacity: str = ""):  # noqa: ANN001, ANN202
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
        """)
        + capacity,
        encoding="utf-8",
    )
    return load(config_path=cfg, env={})


def _build(tmp_path, monkeypatch, capacity: str = ""):  # noqa: ANN001, ANN202
    from workspace_app.__main__ import build_app

    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    return build_app(_settings(tmp_path, capacity), config_dir=None)


def test_a_request_on_the_real_app_is_a_json_line_on_stdout(tmp_path, monkeypatch, capsys):
    app = _build(tmp_path, monkeypatch)

    TestClient(app).get("/api/me")
    app.state.usage_recorder.close()

    docs = [json.loads(x) for x in capsys.readouterr().out.splitlines() if "workspace.usage" in x]
    rows = {(d["usage"]["work"], d["usage"]["user"]) for d in docs if d["usage"]["kind"] == "work"}
    assert ("req:GET /api/me", "default-user") in rows
    assert {d["usage"]["component"] for d in docs} == {"api"}


def test_recording_turned_off_counts_nothing(tmp_path, monkeypatch):
    app = _build(tmp_path, monkeypatch, "capacity:\n  record: false\n")

    assert app.state.usage_recorder is None


def test_a_sink_named_in_config_is_the_one_written(tmp_path, monkeypatch):
    CapturingSink.docs.clear()
    app = _build(
        tmp_path, monkeypatch, "capacity:\n  sink: tests.capacity.test_wiring.CapturingSink\n"
    )

    TestClient(app).get("/api/me")
    app.state.usage_recorder.close()

    assert any(d["usage"].get("work") == "req:GET /api/me" for d in CapturingSink.docs)


def test_a_sink_that_is_not_a_usage_sink_refuses_to_boot(tmp_path, monkeypatch):
    with pytest.raises(TypeError, match="capacity.sink"):
        _build(tmp_path, monkeypatch, "capacity:\n  sink: json.JSONDecoder\n")


def test_the_dashboard_reads_the_sources_config_names(tmp_path, monkeypatch):
    from .world import DemoPods, DemoRecords

    app = _build(
        tmp_path,
        monkeypatch,
        "capacity:\n"
        "  usage_records: tests.capacity.world.DemoRecords\n"
        "  resource_metrics: tests.capacity.world.DemoPods\n"
        "  timezone: Asia/Taipei\n",
    )

    service = app.state.capacity
    assert isinstance(service._sources.usage_records, DemoRecords)  # noqa: SLF001
    assert isinstance(service._sources.resource_metrics, DemoPods)  # noqa: SLF001
    assert service._sources.gpu_metrics is None  # noqa: SLF001
    is_workday = service._workday()  # noqa: SLF001
    import datetime as dt

    assert is_workday(dt.date(2026, 10, 12)) and not is_workday(dt.date(2026, 10, 11))


def test_only_an_app_that_exists_is_counted_as_one(tmp_path, monkeypatch):
    """A route under /api/a/{slug} that answers for any slug — or a 5xx while
    the database is down — named whatever the URL said as an App: a row per
    window, a series, an entry in the what-if's App picker. Only an App the
    platform ships is one."""
    from workspace_app.apps.catalog import discover_app_slugs

    CapturingSink.docs.clear()
    app = _build(
        tmp_path, monkeypatch, "capacity:\n  sink: tests.capacity.test_wiring.CapturingSink\n"
    )
    real = discover_app_slugs()[0]
    client = TestClient(app)

    bogus = client.delete("/api/a/zz-bogus/items/x/env/private")
    profiles = client.get(f"/api/a/{real}/profiles")
    app.state.usage_recorder.close()

    assert bogus.status_code < 400  # it answers for any slug
    assert profiles.status_code == 200
    apps = {
        d["usage"]["work"]: d["usage"]["app"]
        for d in CapturingSink.docs
        if d["usage"]["kind"] == "work"
    }
    assert apps["req:DELETE /api/a/{slug}/items/{item_id}/env/private"] == ""
    assert apps["req:GET /api/a/{slug}/profiles"] == real
