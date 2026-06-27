"""``grouped_line`` — a value series over a multi-level hierarchical x axis.

y is numeric, x is the sequence of rows (input order preserved — "x is a
sequence"). Several key columns form a hierarchy (``levels``, finest → coarsest,
e.g. ``[timestamp, item_id, item_type]``). For each level, a value spanning
several positions is drawn as a ``|⎯ name ⎯|`` bracket whose pipes sit on the
group's left/right boundaries (so its extent is visible — a coarse level like a
lot brackets across all of its wafers); a value on a single position is a bare
label. ``line_level`` selects which level's value-runs break the series into
separate, individually-coloured lines.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from pydantic import BaseModel, Field

from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt

_LABEL_FS = 8.0  # x-axis label font size (pt)


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


def label_text(value: object) -> str:
    """A span's display text (empty for NaN/None)."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value)


def span_bounds(start: int, end: int) -> tuple[float, float]:
    """The x extent a span occupies: each position is a unit cell centred on its
    integer x, so a span [start, end] runs from ``start-0.5`` to ``end+0.5``."""
    return start - 0.5, end + 0.5


def _level_vertical(
    spans: list[tuple[int, int, object]],
    in_per_pos: float,
    char_w_in: float,
    orientation: str,
) -> bool:
    """Whether a level's labels should be drawn vertically. ``auto`` rotates the
    whole level when ANY of its labels is wider than the span it has to fit in
    (many points + long names → no horizontal room)."""
    if orientation == "vertical":
        return True
    if orientation == "horizontal":
        return False
    return any(len(label_text(v)) * char_w_in > (e - s + 1) * in_per_pos * 0.9 for s, e, v in spans)


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
    label_orientation: Literal["auto", "horizontal", "vertical"] = Field(
        "auto",
        description="x label orientation. 'auto' turns a level vertical when its "
        "names are too long to fit horizontally (many points / long group names).",
    )


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
            raise ValueError(f"line_level {line_col!r} is not one of the levels {level_cols}")
        else:
            ax.plot(x, y, marker=marker, ms=4)
        ax.set_ylabel(str(ycol))
        if options.title:
            ax.set_title(options.title)

        # Multi-level x labels beneath the axis (finest first). A value spanning
        # several positions is shown as a |⎯ name ⎯| bracket whose pipes sit on
        # the group's left/right boundaries, so its extent is visible (a coarse
        # group like a lot brackets across all its wafers); a value on a single
        # position is a bare label. A level whose names can't fit horizontally
        # (many points / long names) is rotated vertical and given a taller row.
        ax.set_xticks(np.arange(n))
        ax.set_xticklabels([])
        fig_w, fig_h = fig.get_size_inches()
        in_per_pos = (fig_w * 0.78) / max(n, 1)  # usable axes width per x position
        char_w_in = _LABEL_FS * 0.6 / 72.0
        ax_h_in = max(fig_h * 0.77, 0.1)
        blend = ax.get_xaxis_transform()  # x in data coords, y in axes fraction
        y = -0.04
        for col in level_cols:
            spans = level_spans(df[col].tolist())
            vertical = _level_vertical(spans, in_per_pos, char_w_in, options.label_orientation)
            if vertical:
                longest = max(len(label_text(v)) for _, _, v in spans)  # spans non-empty (n>0)
                row = (longest * char_w_in + 0.06) / ax_h_in  # rotated: length → height
            else:
                row = (_LABEL_FS * 1.7 / 72.0) / ax_h_in
            yc = y - row / 2
            rot = 270 if vertical else 0  # 270° (CW) reads top-to-bottom
            for s, e, v in spans:
                if e > s:  # group span → boundary bracket
                    xl, xr = span_bounds(s, e)
                    ax.plot(
                        [xl, xl], [y, y - row], transform=blend, color="0.45", lw=1.0, clip_on=False
                    )
                    ax.plot(
                        [xr, xr], [y, y - row], transform=blend, color="0.45", lw=1.0, clip_on=False
                    )
                    ax.plot([xl, xr], [yc, yc], transform=blend, color="0.6", lw=0.8, clip_on=False)
                    ax.text(
                        (s + e) / 2,
                        yc,
                        label_text(v),
                        transform=blend,
                        ha="center",
                        va="center",
                        rotation=rot,
                        fontsize=_LABEL_FS,
                        clip_on=False,
                        bbox={"boxstyle": "square,pad=0.15", "fc": "white", "ec": "none"},
                    )
                else:  # single position → bare label
                    ax.text(
                        s,
                        yc,
                        label_text(v),
                        transform=blend,
                        ha="center",
                        va="center",
                        rotation=rot,
                        fontsize=_LABEL_FS,
                        clip_on=False,
                    )
            y -= row + 0.02
        return fig


register(GroupedLine())
