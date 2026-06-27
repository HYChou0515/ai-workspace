"""Tests for the chart registry, request-model assembly, and dispatch."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from matplotlib.figure import Figure
from pydantic import BaseModel

import sci_plot.charts  # noqa: F401  (populate the registry)
from sci_plot.framework import registry
from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import build_request_model, charts, register, run_chart
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt

NOW = datetime(2026, 6, 27, 14, 0, 0, 1)


def test_box_scatter_is_registered():
    assert "box_scatter" in charts()


def test_register_requires_name():
    class Nameless(IChart):
        def draw(self, df, roles, options) -> Figure:  # pragma: no cover
            return plt.figure()

    with pytest.raises(ValueError, match="no name"):
        register(Nameless())


def test_single_variant_request_model_round_trips():
    model = build_request_model()
    req = model.model_validate(
        {"chart": "box_scatter", "data": {"g": ["a", "b"], "y": [1, 2]}, "group": "g", "y": "y"}
    )
    assert req.root.chart == "box_scatter"


def test_run_chart_writes_image(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = build_request_model()
    req = model.model_validate(
        {
            "chart": "box_scatter",
            "data": {"g": ["a", "a", "b"], "y": [1, 2, 3]},
            "group": "g",
            "y": "y",
        }
    )
    result = run_chart(req, NOW)
    assert "images" in result and len(result["images"]) == 1
    assert Path(result["images"][0]).exists()


def test_run_chart_accepts_style_override(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = build_request_model()
    req = model.model_validate(
        {
            "chart": "box_scatter",
            "data": {"g": ["a", "b"], "y": [1, 2]},
            "group": "g",
            "y": "y",
            "style": {"dpi": 80, "x_tick_rotation": 30},
        }
    )
    result = run_chart(req, NOW)
    assert "images" in result and Path(result["images"][0]).exists()


def test_run_chart_needs_input_when_ambiguous(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = build_request_model()
    req = model.model_validate({"chart": "box_scatter", "data": {"n1": [1, 2], "n2": [3, 4]}})
    result = run_chart(req, NOW)
    assert "needs_input" in result
    assert {it["role"] for it in result["needs_input"]["needed"]} == {"group", "y"}


def test_build_request_model_is_discriminated_union_with_two_charts():
    """Register a temporary second chart → the model becomes a discriminated
    union on `chart` (restore the registry afterward)."""

    class FakeOpts(BaseModel):
        pass

    class Fake(IChart):
        name = "fake_chart"
        description = "fake"
        roles = (Role("x", RoleKind.NUMBER, required=True),)
        Options = FakeOpts

        def draw(self, df, roles, options) -> Figure:  # pragma: no cover
            return plt.figure()

    saved = dict(registry._REGISTRY)
    try:
        register(Fake())
        schema = build_request_model().model_json_schema()
        # oneOf over the two variants + a discriminator mapping on `chart`.
        assert "oneOf" in schema or "anyOf" in schema or "discriminator" in schema
        names = {v.get("const") for v in _chart_consts(schema)}
        assert {"box_scatter", "fake_chart"} <= names
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


def _chart_consts(schema: dict) -> list[dict]:
    """Pull every `chart` property const out of the $defs variants."""
    out = []
    for d in schema.get("$defs", {}).values():
        ch = d.get("properties", {}).get("chart", {})
        if "const" in ch:
            out.append(ch)
    return out


def test_no_charts_registered_raises():
    saved = dict(registry._REGISTRY)
    try:
        registry._REGISTRY.clear()
        with pytest.raises(ValueError, match="no charts registered"):
            build_request_model()
    finally:
        registry._REGISTRY.update(saved)


def test_single_chart_request_model_is_a_bare_variant():
    """With one chart registered, the model is that variant (no discriminator)."""
    from sci_plot.charts.box_scatter import BoxScatter

    saved = dict(registry._REGISTRY)
    try:
        registry._REGISTRY.clear()
        register(BoxScatter())
        model = build_request_model()
        req = model.model_validate(
            {"chart": "box_scatter", "data": {"g": ["a"], "y": [1]}, "group": "g", "y": "y"}
        )
        assert req.root.chart == "box_scatter"
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)
