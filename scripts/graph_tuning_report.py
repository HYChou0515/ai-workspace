"""#697 — turn a folder of tuning rounds into a report a person can hand round.

    uv run python scripts/graph_tuning_report.py ./rounds report.html

Reads what `--tune-round` left on disk and writes ONE self-contained HTML file:
no network, no CDN, no webfont, so it survives being emailed.

The chart that matters is the first one. It plots the SAME measure — the share
of extracted names that are neither document furniture nor one-off noise — on
both document sets at once. The tuning batch rotates every round; the holdout
never does. Two lines rising together is a criterion that generalises; the
tuning line rising while the holdout line flattens is the criterion learning the
passages it kept being shown, which is the failure the holdout exists to expose
and which no single number can show.

The template is a sibling `.html` file substituted with `str.replace`, not
`str.format`: a page is mostly CSS, CSS is mostly braces, and a template that
makes a person double every one of them is a template they will eventually get
wrong.
"""

from __future__ import annotations

import html
import json
import pathlib
import sys
from typing import Any

from workspace_app.kb.graph.tune import fitness

TEMPLATE = pathlib.Path(__file__).with_suffix(".html")

# The plot box, in user units. One shape for both panels so they read as a pair.
WIDTH, HEIGHT = 760, 220
PAD_L, PAD_R, PAD_T, PAD_B = 46, 16, 14, 30

#: How far the tuning line may sit above the holdout line before the report
#: says so. Below this the two are moving together; above it they have parted.
DIVERGED = 0.12
#: How many versions the table lists. The chart carries the whole history; the
#: table is for reading the recent ones closely.
LISTED = 25

Point = tuple[float, float]


def load(rounds_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Every version that has been scored, oldest first.

    Sorted NUMERICALLY. `sorted(glob("v*"))` is lexicographic, which puts v10
    before v2 and turns every line on every chart into a zigzag that still looks
    like data.
    """
    out: list[dict[str, Any]] = []
    folders = [p for p in rounds_dir.glob("v*") if p.name[1:].isdigit()]
    for folder in sorted(folders, key=lambda p: int(p.name[1:])):
        card = folder / "scorecard.json"
        if not card.is_file():
            continue  # written but never graded — that is the NEXT round's job
        data = json.loads(card.read_text())
        holdout = data.get("holdout")
        parent = folder / "parent.txt"
        out.append(
            {
                "v": int(folder.name[1:]),
                "holdout": holdout,
                "tune": data.get("tune") or {},
                "parent": int(parent.read_text()) if parent.is_file() else None,
                "fitness": fitness(holdout) if holdout else None,
            }
        )
    return out


def clean_share(card: dict[str, Any] | None) -> float | None:
    """The share of names that are neither furniture nor one-off noise.

    The two GOOD quadrants, in other words — the corpus's subject and the names
    that discriminate between its documents. `None` when the version predates
    the quadrants, so an old row is left off the chart rather than drawn at zero.
    """
    if not card or "furniture_share" not in card:
        return None
    noise = float(card.get("furniture_share", 0)) + float(card.get("singleton_share", 0))
    return max(0.0, min(1.0, 1.0 - noise))


def _place(points: list[Point], xs: list[int]) -> list[Point]:
    span = (max(xs) - min(xs)) or 1
    plot_w, plot_h = WIDTH - PAD_L - PAD_R, HEIGHT - PAD_T - PAD_B
    return [(PAD_L + (x - min(xs)) / span * plot_w, PAD_T + (1 - y) * plot_h) for x, y in points]


def panel(series: list[tuple[str, list[Point]]], xs: list[int], best: int | None) -> str:
    """One chart. Every y is already a 0..1 share, so the axis needs no scaling."""
    plot_h = HEIGHT - PAD_T - PAD_B
    bits: list[str] = []
    for value in (0.0, 0.5, 1.0):
        y = PAD_T + (1 - value) * plot_h
        bits.append(
            f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" x2="{WIDTH - PAD_R}" y2="{y:.1f}"/>'
        )
        bits.append(
            f'<text class="tick" x="{PAD_L - 8}" y="{y + 3.5:.1f}" '
            f'text-anchor="end">{value:g}</text>'
        )
    if best is not None and len(xs) > 1:
        ((px, _),) = _place([(best, 0.0)], xs)
        bits.append(
            f'<line class="best" x1="{px:.1f}" y1="{PAD_T}" x2="{px:.1f}" y2="{HEIGHT - PAD_B}"/>'
        )
        bits.append(
            f'<text class="besttag" x="{px:.1f}" y="{PAD_T - 3}" '
            f'text-anchor="middle">最佳 v{best}</text>'
        )
    for token, points in series:
        if len(points) < 2:
            continue  # a single point is not a trend and draws as nothing anyway
        placed = _place(points, xs)
        path = " ".join(
            ("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(placed)
        )
        bits.append(f'<path class="line {token}" d="{path}"/>')
        ex, ey = placed[-1]
        bits.append(f'<circle class="dot {token}" cx="{ex:.1f}" cy="{ey:.1f}" r="3"/>')
    low, high = min(xs), max(xs)
    for value in dict.fromkeys((low, (low + high) // 2, high)):
        ((px, _),) = _place([(value, 0.0)], xs)
        bits.append(
            f'<text class="tick" x="{px:.1f}" y="{HEIGHT - PAD_B + 16}" '
            f'text-anchor="middle">v{value}</text>'
        )
    return (
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" preserveAspectRatio="xMidYMid meet">'
        + "".join(bits)
        + "</svg>"
    )


def cell(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}".rstrip("0").rstrip(".")


def verdict(tune: list[Point], holdout: list[Point]) -> tuple[str, str]:
    """The one sentence a reader wants: are the two lines still together?"""
    if not tune or not holdout:
        return "note", ""
    gap = tune[-1][1] - holdout[-1][1]
    if gap > DIVERGED:
        return "warn", (
            f"目前 tune 比 holdout 乾淨 {gap:.0%} —— 兩條線正在分開,"
            "這是提示詞開始學那批反覆看到的文件的形狀。回頭看兩條線還併在一起的那幾版。"
        )
    return (
        "note",
        f"目前兩條線相距 {abs(gap):.0%},仍然一起移動 —— 學到的東西還推廣得到沒看過的文件。",
    )


def render(rounds_dir: pathlib.Path) -> str:
    rows = load(rounds_dir)
    graded = [r for r in rows if r["holdout"]]
    if not graded:
        raise SystemExit(f"{rounds_dir} 裡沒有任何版本跑過 holdout")

    xs = [r["v"] for r in graded]
    hit = [
        (r["v"], float(r["holdout"]["lookup_hit_rate"]))
        for r in graded
        if r["holdout"].get("lookup_hit_rate") is not None
    ]
    clean_hold = [(r["v"], s) for r in graded if (s := clean_share(r["holdout"])) is not None]
    clean_tune = [(r["v"], s) for r in graded if (s := clean_share(r["tune"])) is not None]
    scored = [r for r in graded if r["fitness"] is not None]
    best = max(scored, key=lambda r: r["fitness"]) if scored else None
    best_v = best["v"] if best else None

    body = []
    for row in reversed(graded[-LISTED:]):
        held = row["holdout"]
        cells = [
            f"v{row['v']}",
            "—" if row["fitness"] is None else f"{row['fitness']:.3f}",
            cell(held.get("lookup_hit_rate")),
            cell(clean_share(held)),
            cell(clean_share(row["tune"])),
            cell(held.get("judged_keep_share")),
            cell(held.get("mentions_per_document")),
            "—" if row["parent"] is None else f"v{row['parent']}",
        ]
        mark = ' class="is-best"' if row["v"] == best_v else ""
        body.append(f"<tr{mark}>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")

    diverge_class, diverge = verdict(clean_tune, clean_hold)
    page = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("TOTAL", str(len(rows))),
        ("GRADED", str(len(graded))),
        ("BEST", f"v{best_v}" if best_v is not None else "—"),
        ("BEST_FITNESS", f"{best['fitness']:.3f}" if best else "—"),
        ("BEST_HIT", cell(best["holdout"].get("lookup_hit_rate")) if best else "—"),
        ("CHART_LEARN", panel([("hold", clean_hold), ("tune", clean_tune)], xs, best_v)),
        ("CHART_RECALL", panel([("hold", hit)], xs, best_v)),
        ("DIVERGE_CLASS", diverge_class),
        ("DIVERGE", html.escape(diverge)),
        ("ROWS", "\n".join(body)),
        (
            "PROMPT_PATH",
            html.escape(f"{rounds_dir}/v{best_v}/prompt.txt" if best_v is not None else "—"),
        ),
    ):
        page = page.replace(f"%%{token}%%", value)
    return page


def main() -> None:
    rounds_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "rounds")
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "graph-tuning-report.html")
    out.write_text(render(rounds_dir), encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
