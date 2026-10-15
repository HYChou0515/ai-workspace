"""`capacity:` (docs/plan-capacity-usage.md §12): whitelisted AND built, so a
key the operator writes reaches `Settings` (a key that parses and goes nowhere
is the dead-knob class), and a value that cannot work refuses to boot with the
key named."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from workspace_app.config.loader import load


def _load(tmp_path: Path, body: str):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(body, encoding="utf-8")
    return load(config_path=cfg, env={})


def test_defaults_record_and_read_nothing_until_a_source_is_named(tmp_path: Path):
    c = load(config_path=tmp_path / "missing.yaml", env={}).capacity

    assert c.record is True
    assert c.sink == ""
    assert c.window_s == 300
    assert c.step_s == 300
    assert c.llm_tags is True
    assert (c.usage_records, c.resource_metrics, c.gpu_metrics, c.llm_requests) == ("", "", "", "")


def test_every_key_the_operator_writes_reaches_settings(tmp_path: Path):
    c = _load(
        tmp_path,
        "capacity:\n"
        "  record: false\n"
        "  sink: my.mod.Sink\n"
        "  window_s: 60\n"
        "  queue_max: 50\n"
        "  llm_tags: false\n"
        "  usage_records: my.mod.Records\n"
        "  resource_metrics: my.mod.Pods\n"
        "  gpu_metrics: my.mod.Gpus\n"
        "  llm_requests: my.mod.Llm\n"
        "  step_s: 120\n"
        "  lag_s: 30\n"
        "  timezone: Asia/Taipei\n"
        "  max_rows: 10\n",
    ).capacity

    assert c.record is False
    assert c.sink == "my.mod.Sink"
    assert c.window_s == 60
    assert c.queue_max == 50
    assert c.llm_tags is False
    assert (c.usage_records, c.resource_metrics, c.gpu_metrics, c.llm_requests) == (
        "my.mod.Records",
        "my.mod.Pods",
        "my.mod.Gpus",
        "my.mod.Llm",
    )
    assert c.step_s == 120
    assert c.lag_s == 30
    assert c.timezone == "Asia/Taipei"
    assert c.max_rows == 10


def test_an_unknown_key_refuses_to_boot(tmp_path: Path):
    with pytest.raises(ValueError, match="capacity"):
        _load(tmp_path, "capacity:\n  windw_s: 60\n")


@pytest.mark.parametrize(
    ("body", "sentence"),
    [
        ("window_s: 7", "capacity.window_s must divide 3600"),
        ("window_s: 0", "capacity.window_s must divide 3600"),
        ("step_s: 450", "capacity.step_s must be a multiple of capacity.window_s"),
        ("timezone: Mars/Olympus", "capacity.timezone"),
        ("queue_max: 0", "capacity.queue_max must be a positive integer"),
        ("max_rows: -1", "capacity.max_rows must be a positive integer"),
        ("lag_s: -5", "capacity.lag_s must be 0 or more"),
    ],
)
def test_a_value_that_cannot_work_refuses_to_boot(tmp_path: Path, body: str, sentence: str):
    with pytest.raises(ValueError, match=re.escape(sentence)):
        _load(tmp_path, f"capacity:\n  {body}\n")


def test_every_capacity_key_is_named_in_the_manual():
    """A knob nothing documents is never set (the sibling of
    tests/config/test_server_settings_are_documented.py)."""
    import dataclasses

    from workspace_app.config.schema import CapacitySettings

    manual = (Path(__file__).resolve().parents[2] / "docs" / "configuration.md").read_text("utf-8")

    missing = [
        f.name for f in dataclasses.fields(CapacitySettings) if f"capacity.{f.name}" not in manual
    ]
    assert missing == []


def test_the_recorder_is_built_from_every_writing_key(tmp_path: Path):
    from workspace_app.factories import build_usage_recorder

    c = _load(tmp_path, "capacity:\n  window_s: 60\n  queue_max: 7\n  llm_tags: false\n").capacity
    rec = build_usage_recorder(c, component="api")
    try:
        assert rec is not None
        assert (rec.window_s, rec.llm_tags, rec.component) == (60, False, "api")
        assert rec._writer._queue.maxsize == 7  # noqa: SLF001
    finally:
        assert rec is not None
        rec.close()
