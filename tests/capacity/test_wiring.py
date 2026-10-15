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
