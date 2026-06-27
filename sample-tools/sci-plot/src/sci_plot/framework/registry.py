"""The chart registry + request-model assembly + dispatch.

* Charts register themselves at import (``register(BoxScatter())``).
* :func:`build_request_model` auto-assembles the ``chart`` command's pydantic
  model — a discriminated union on ``chart`` where each variant carries that
  chart's ``data`` + role fields + ``Options``. This is the JSON schema the LLM
  sees, so adding a chart grows the union with zero hand-written schema.
* :func:`run_chart` is the dispatch: normalize → resolve roles → (ask | render
  → save) → structured result dict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Union

from pydantic import BaseModel, Field, RootModel, create_model

from sci_plot.framework.chart import IChart
from sci_plot.framework.normalize import DataInput, normalize
from sci_plot.framework.output import resolve_output
from sci_plot.framework.resolve import AskNeeded, Resolved, resolve
from sci_plot.framework.roles import Role
from sci_plot.framework.style import StyleOverride, merged_style, render, save

_REGISTRY: dict[str, IChart] = {}


def register(chart: IChart) -> IChart:
    """Register a chart instance under its ``name`` (idempotent overwrite)."""
    if not chart.name:
        raise ValueError(f"chart {type(chart).__name__} has no name")
    _REGISTRY[chart.name] = chart
    return chart


def charts() -> dict[str, IChart]:
    return dict(_REGISTRY)


def _role_field_type(role: Role) -> Any:
    return (list[str] | None) if role.multi else (str | None)


def _build_variant(chart: IChart) -> type[BaseModel]:
    from typing import Literal

    fields: dict[str, Any] = {
        "chart": (Literal[chart.name], chart.name),
        "data": (
            DataInput,
            Field(
                description=(
                    "Data to plot: a workspace file path "
                    "(.csv/.tsv/.json/.xlsx/.parquet) OR inline JSON — a list of "
                    'row records [{"col": value, …}, …] or a column dict '
                    '{"col": [values…], …}.'
                )
            ),
        ),
        "output": (
            str | None,
            Field(None, description="Optional output path; defaults to charts/<chart>_<time>.png"),
        ),
        "options": (chart.Options, Field(default_factory=chart.Options)),
        "style": (
            StyleOverride | None,
            Field(
                None,
                description=(
                    "Presentation overrides (figsize/dpi/font_size/x_tick_rotation/pad). "
                    "Normally left unset — the VLM auto-review loop fills these to fix "
                    "layout issues."
                ),
            ),
        ),
    }
    for role in chart.roles:
        suffix = "" if role.required else " (optional)"
        fields[role.name] = (
            _role_field_type(role),
            Field(None, description=f"[{role.kind.value}] {role.description}{suffix}"),
        )
    return create_model(f"{chart.name}_request", **fields)


def build_request_model() -> type[RootModel]:
    """The ``chart`` command's pydantic model — a discriminated union over the
    registered charts (a single variant when only one is registered)."""
    registered = list(charts().values())
    if not registered:
        raise ValueError("no charts registered")
    variants = [_build_variant(c) for c in registered]
    if len(variants) == 1:
        root_type: Any = variants[0]
    else:
        root_type = Annotated[Union[tuple(variants)], Field(discriminator="chart")]
    return RootModel[root_type]


def _ask_payload(ask: AskNeeded) -> dict[str, Any]:
    return {
        "message": (
            "Some required columns couldn't be matched automatically. Specify "
            "them (by column name) and call again."
        ),
        "needed": [
            {
                "role": it.role,
                "kind": it.kind,
                "required": it.required,
                "reason": it.reason,
                "candidates": it.candidates,
            }
            for it in ask.items
        ],
        "available_columns": [{"name": c.name, "dtype": c.dtype} for c in ask.available],
    }


def run_chart(root: RootModel, now: datetime) -> dict[str, Any]:
    """Dispatch a validated request to its chart and return a structured result:
    ``{"images": [path]}`` on success, or ``{"needs_input": {…}}`` when roles
    are ambiguous/missing."""
    variant = root.root
    chart = _REGISTRY[variant.chart]
    assignments = {r.name: getattr(variant, r.name) for r in chart.roles}
    df = normalize(variant.data)
    resolved = resolve(df, chart.roles, assignments)
    if isinstance(resolved, AskNeeded):
        return {"needs_input": _ask_payload(resolved)}
    assert isinstance(resolved, Resolved)
    style = merged_style(getattr(variant, "style", None))
    fig = render(chart, resolved.df, resolved.roles, variant.options, style)
    out = resolve_output(chart.name, variant.output, now)
    save(fig, str(out), style)
    return {"images": [str(out)]}
