# sci-plot — scientific plotting tool (#285)

`sci-plot` is an extensible catalog of domain charts the agent renders from
tabular data. It is a sandbox **tool package** (`sample-tools/sci-plot/`) exposing
a single `chart` command, plus a backend **VLM self-review loop** that
auto-corrects layout issues before the chart is returned.

It is enabled for the **RCA** and **Playground** apps (in their `allowed_tools`).

## Using it (the agent's view)

The agent calls one tool, `chart`, picking a chart type and supplying data:

```json
{
  "chart": "box_scatter",
  "data": {"tool": ["E1","E1","E2"], "defects": [2, 3, 9]},
  "group": "tool",
  "y": "defects"
}
```

- **`data`** — a workspace file path (`.csv/.tsv/.json/.xlsx/.parquet`) **or**
  inline JSON: a list of row records `[{"col": v}, …]` or a column dict
  `{"col": [v, …]}`.
- **Column roles** (`group`, `y`, `die_x`, …) are **optional**: given → used;
  omitted + unambiguous → inferred; omitted + ambiguous → the result is a
  `needs_input` object listing the candidate columns, and the agent re-calls
  with explicit names. Types are coerced liberally (numeric-ish strings →
  numbers, date-ish → datetime).
- Output is `{"images": ["charts/<chart>_<timestamp>.png"]}`. The chat renders
  the PNG inline (any tool that reports `images`/`plots` paths is rendered — see
  `web/src/renderers/toolImages.ts`).

### v1 catalog

| chart | what it shows | key roles | notable options |
|-------|---------------|-----------|-----------------|
| `box_scatter` | box + per-group points, one colour per group | `group`, `y` | `max_points` (above it, a group shows only outliers — default 1000) |
| `grouped_line` | a numeric series over a multi-level hierarchical x axis | `levels` (finest→coarsest), `value` | `line_level` (which level splits into separate coloured lines) |
| `wafermap` | a die grid in a wafer circle, coloured by value | `die_x`, `die_y`, `value` | `color_mode` `uni` (≥0, defect count) / `bi` (diverging, measurement), `wafer_diameter`, `notch` |
| `defectmap` | each defect as a small square at its coordinate | `x`, `y` | `die_pitch` (faint reference grid), `color`, `marker_size` |

`grouped_line`'s hierarchical tick labels collapse a value that spans several
positions into one `|value|` bracket (a group shown once), and show unique
values bare.

## Adding a chart (the developer's view)

A chart is one `IChart` subclass. Add a module under
`src/sci_plot/charts/`, register it, and the `chart` command's JSON schema
(a discriminated union on `chart`) grows automatically:

```python
from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt
from pydantic import BaseModel

class MyOptions(BaseModel):
    ...

class MyChart(IChart):
    name = "my_chart"
    description = "one line the LLM reads to pick + fill this chart"
    roles = (Role("x", RoleKind.NUMBER), Role("y", RoleKind.NUMBER))
    Options = MyOptions

    def draw(self, df, roles, options):
        fig, ax = plt.subplots()      # bare → inherits the house style
        ax.plot(df[roles["x"]], df[roles["y"]])
        return fig

register(MyChart())
```

Then add `from sci_plot.charts import my_chart` to `charts/__init__.py` and
re-run the prebuild (`uv run python scripts/prebuild_tools.py`).

**The framework does the boring 80%** uniformly for every chart: read the file
or inline data → coerce each role column to its declared kind → resolve which
column fills each role (explicit / inferred / ask) → apply the house style →
`savefig`. Your `draw` only declares `roles` + `Options` and plots the content
(it keeps full freedom — build a die grid, compute cumulative %, collapse
labels, suppress points — and may override frame bits like equal aspect).

## VLM self-review loop (#285)

When a vision model is wired (`describer`), a chart that emits an image is
auto-reviewed before being returned:

1. **render** in the sandbox;
2. **detect** — the VLM answers a fixed yes/no checklist (blank, label overlap,
   truncation, tiny text, clipping, missing legend/colorbar) +a free note
   (`agent/plot_review.py: detect_issues`);
3. **adjust** — a *deterministic* rule maps detected issues → presentation knobs
   (figsize / dpi / tick rotation / font size / padding), with the VLM note as a
   soft hint (`adjust_style`). It never touches which column is which.
4. **re-render** with the new `style`, up to **2 correction passes**.

The loop keeps the **best** attempt and never makes a chart worse; if it can't
fully fix the layout it returns the best render and a one-line summary of what
remains ("Visual check (2 passes): auto-fixed overlap; still tiny_text."). The
small VLM only ever *detects* (reliable); the *fix* is deterministic and
unit-tested. The model is a pluggable external dependency (local
qwen2.5vl via Ollama) — needing multimodal capability does not mean needing a
hosted model.

See `docs/plan-sci-plot.md` for the grilled design decisions.
