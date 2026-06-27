"""``grouped_line`` — a value series over a multi-level hierarchical x axis.

y is numeric, x is the sequence of rows (input order preserved — "x is a
sequence"). Several key columns form a hierarchy (``levels``, finest → coarsest,
e.g. ``[timestamp, item_id, item_type]``). Each level's tick labels collapse
consecutive repeats into a single ``|value|`` bracket centered over its span
(so a group name shows once, not on every tick); a value unique to its position
is shown bare. ``line_level`` selects which level's value-runs break the series
into separate, individually-coloured lines.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from pydantic import BaseModel, Field

from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt


def _eq(a: object, b: object) -> bool:
    """Equality that treats two NaNs as equal (so a NaN run still collapses)."""
    a_nan = isinstance(a, float) and a != a
    b_nan = isinstance(b, float) and b != b
    if a_nan or b_nan:
        return a_nan and b_nan
    return bool(a == b)


def level_spans(values: Sequence[object]) -> list[tuple[int, int, object]]:
    """Maximal runs of consecutive equal values → ``(start, end_inclusive, value)``."""
    spans: list[tuple[int, int, object]] = []
    i, n = 0, len(values)
    while i < n:
        j = i
        while j + 1 < n and _eq(values[j + 1], values[i]):
            j += 1
        spans.append((i, j, values[i]))
        i = j + 1
    return spans


def span_label(value: object, start: int, end: int) -> str:
    """A span's tick label: ``|value|`` when it covers >1 position (a group), the
    bare value when it sits on a single position. NaN renders as empty."""
    text = "" if (value is None or (isinstance(value, float) and value != value)) else str(value)
    return f"|{text}|" if end > start else text


class GroupedLineOptions(BaseModel):
    line_level: str | None = Field(
        None,
        description=(
            "Which level column's value-runs break the data into separate lines "
            "(each a colour). None → a single line through all points in order."
        ),
    )
    title: str | None = Field(None, description="Optional figure title.")
    marker: str = Field("o", description="Point marker (matplotlib marker code; '' for none).")


class GroupedLine(IChart):
    name = "grouped_line"
    description = (
        "A numeric series over a multi-level hierarchical x axis. `levels` are the "
        "key columns finest→coarsest (e.g. [timestamp, item_id, item_type]); repeated "
        "group values collapse to one |name| bracket. `line_level` splits the series "
        "into separate coloured lines at that level's boundaries."
    )
    roles = (
        Role(
            "levels",
            RoleKind.ANY,
            required=True,
            multi=True,
            description="ordered key columns, finest→coarsest, forming the x hierarchy",
        ),
        Role("value", RoleKind.NUMBER, required=True, description="numeric value for the y axis"),
    )
    Options = GroupedLineOptions

    def draw(self, df: pd.DataFrame, roles: dict, options: GroupedLineOptions) -> Figure:
        level_cols: list[str] = list(roles["levels"])
        ycol = roles["value"]
        n = len(df)
        if n == 0:
            raise ValueError("no rows to plot")
        x = np.arange(n)
        y = pd.to_numeric(df[ycol], errors="coerce").to_numpy(dtype=float)

        fig, ax = plt.subplots()
        marker = options.marker or None
        line_col = options.line_level
        if line_col is not None and line_col in level_cols:
            cmap = plt.get_cmap("tab10")
            for k, (s, e, val) in enumerate(level_spans(df[line_col].tolist())):
                sl = slice(s, e + 1)
                ax.plot(x[sl], y[sl], marker=marker, ms=4, color=cmap(k % 10), label=str(val))
            ax.legend(title=str(line_col), fontsize=8)
        elif line_col is not None:
            raise ValueError(
                f"line_level {line_col!r} is not one of the levels {level_cols}"
            )
        else:
            ax.plot(x, y, marker=marker, ms=4)
        ax.set_ylabel(str(ycol))
        if options.title:
            ax.set_title(options.title)

        # Finest level → the primary x tick labels (collapsed spans).
        finest_spans = level_spans(df[level_cols[0]].tolist())
        ax.set_xticks([(s + e) / 2 for s, e, _ in finest_spans])
        ax.set_xticklabels([span_label(v, s, e) for s, e, v in finest_spans])
        # Coarser levels → stacked label rows beneath the axis, with separators.
        blend = ax.get_xaxis_transform()  # x in data coords, y in axes fraction
        for depth, col in enumerate(level_cols[1:], start=1):
            yoff = -0.07 - 0.06 * depth
            for s, e, v in level_spans(df[col].tolist()):
                ax.text(
                    (s + e) / 2, yoff, span_label(v, s, e),
                    transform=blend, ha="center", va="top", fontsize=8, clip_on=False,
                )
                if s > 0:  # a boundary tick between adjacent groups
                    ax.axvline(s - 0.5, color="0.85", lw=0.8, zorder=0)
        return fig


register(GroupedLine())
