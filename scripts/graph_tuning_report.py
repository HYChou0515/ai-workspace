"""#697 — turn a folder of tuning rounds into a report a person can hand round.

    uv run python scripts/graph_tuning_report.py ./rounds report.html

Reads what `--tune-round` left on disk and writes ONE self-contained HTML file:
no network, no CDN, no webfont, so it survives being emailed.

Three things make it readable rather than merely complete.

**Every axis is scaled to its own data.** A share plotted on a fixed 0..1 axis
is a flat line whenever the interesting movement is a few points wide — which,
after the first handful of rounds, it always is. The axis states the range it
chose, so a small change never masquerades as a large one.

**The lead chart plots one measure on BOTH document sets.** The tuning batch
rotates every round; the holdout never does. Two lines rising together is a
criterion that generalises; the tuning line rising while the holdout flattens is
the criterion learning the passages it kept being shown — the failure the
holdout exists to expose, and one no single number can show.

**The selection is stated, not implied.** Which version to keep is the question
the whole run exists to answer, so it is a block near the top with its scores,
its file, and the ancestry that produced it.

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

# Two plot shapes: the lead chart, and the small multiples below it.
BIG = (760, 230, 52, 16, 16, 30)
SMALL = (360, 150, 46, 12, 14, 26)

#: How far the tuning line may sit above the holdout line before the report
#: says so. Below this the two are moving together; above it they have parted.
DIVERGED = 0.12
#: How many versions the table lists. The charts carry the whole history; the
#: table is for reading the recent ones closely.
LISTED = 25

Point = tuple[float, int | float]

#: Every index worth a trend line, in the order a reader should meet them.
#: ``both`` means the measure exists on the tuning batch as well, so the panel
#: can show the pair — and a pair is the only way overfitting is visible.
METRICS: tuple[tuple[str, str, str, str], ...] = (
    ("lookup_hit_rate", "命中率", "holdout", "探針查得到的比例。收太緊唯一會下跌的數字"),
    ("fitness", "fitness", "derived", "命中率 ×(1 − 家具 − 單次)。beam 挑版本的依據"),
    ("furniture_share", "文件家具佔比", "both", "多數文件都有、每篇只提一次 —— 標題、欄名、泛稱"),
    ("singleton_share", "一次性雜訊佔比", "both", "少數文件才有、而且只提一次 —— 值、路過的名詞"),
    (
        "discriminative_share",
        "鑑別度高的佔比",
        "both",
        "少數文件才有、但那篇反覆提 —— 最有價值的一格",
    ),
    ("core_share", "核心主題佔比", "both", "多數文件都有、而且反覆提"),
    ("judged_keep_share", "judge 留存率", "holdout", "另一個模型認為「查得到」的比例"),
    (
        "ordinary_share",
        "與一般文章重疊",
        "holdout",
        "對照語料裡也常見的比例。沒放對照語料就不會出現",
    ),
    ("mentions_per_document", "每篇抽出幾個", "both", "量,不是質 —— 單獨看會獎勵什麼都不抽"),
    ("distinct_names", "不重複名字數", "both", "與上一項分岔,代表每篇都在貢獻沒人重複的新名字"),
    ("mentions_starting_with_a_digit", "數字開頭的名字", "both", "把量測值當東西的直接證據"),
)


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

    The two GOOD quadrants — the corpus's subject, and the names that
    discriminate between its documents. `None` when the version predates the
    quadrants, so an old row is left OFF the chart rather than drawn at zero: a
    missing measurement plotted as zero invents a valley that never happened.
    """
    if not card or "furniture_share" not in card:
        return None
    noise = float(card.get("furniture_share", 0)) + float(card.get("singleton_share", 0))
    return max(0.0, min(1.0, 1.0 - noise))


def series_for(rows: list[dict[str, Any]], key: str, half: str) -> list[Point]:
    """One metric's points, skipping versions that never measured it."""
    out: list[Point] = []
    for row in rows:
        if key == "fitness":
            value = row["fitness"]
        else:
            card = row["holdout"] if half == "holdout" else row["tune"]
            value = (card or {}).get(key)
        if value is not None:
            out.append((row["v"], float(value)))
    return out


def bounds(series: list[list[Point]]) -> tuple[float, float] | None:
    """The y range to draw, padded — never a fixed 0..1.

    Most of these settle into a few points of movement after the opening rounds,
    and on a full-height axis that reads as a flat line: the report would show a
    run that changed nothing when the run changed what mattered.
    """
    ys = [y for points in series for _, y in points]
    if not ys:
        return None
    low, high = float(min(ys)), float(max(ys))
    if high - low < 1e-9:  # a constant series still deserves a visible line
        pad = max(abs(high) * 0.1, 0.05)
        return low - pad, high + pad
    pad = (high - low) * 0.14
    return low - pad, high + pad


def tick_text(value: float) -> str:
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def panel(
    series: list[tuple[str, list[Point]]],
    xs: list[int],
    *,
    box: tuple[int, int, int, int, int, int],
    best: int | None = None,
    label_x: bool = True,
) -> str:
    """One chart, scaled to its own data."""
    width, height, pad_l, pad_r, pad_t, pad_b = box
    drawn = [points for _, points in series if points]
    span = bounds(drawn)
    if span is None:
        return ""
    low, high = span
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    x_span = (max(xs) - min(xs)) or 1

    def at(x: float, y: float) -> tuple[float, float]:
        px = pad_l + (x - min(xs)) / x_span * plot_w
        py = pad_t + (1 - (y - low) / (high - low)) * plot_h
        return px, py

    bits: list[str] = []
    for value in (low, (low + high) / 2, high):
        _, py = at(min(xs), value)
        bits.append(
            f'<line class="grid" x1="{pad_l}" y1="{py:.1f}" x2="{width - pad_r}" y2="{py:.1f}"/>'
        )
        bits.append(
            f'<text class="tick" x="{pad_l - 6}" y="{py + 3.5:.1f}" '
            f'text-anchor="end">{tick_text(value)}</text>'
        )
    if best is not None and len(xs) > 1:
        px, _ = at(best, low)
        bits.append(
            f'<line class="best" x1="{px:.1f}" y1="{pad_t}" x2="{px:.1f}" y2="{height - pad_b}"/>'
        )
    for token, points in series:
        if len(points) < 2:
            continue  # a single point is not a trend and draws as nothing anyway
        placed = [at(x, y) for x, y in points]
        path = " ".join(
            ("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(placed)
        )
        bits.append(f'<path class="line {token}" d="{path}"/>')
        ex, ey = placed[-1]
        bits.append(f'<circle class="dot {token}" cx="{ex:.1f}" cy="{ey:.1f}" r="2.6"/>')
        if best is not None:
            for x, y in points:
                if x == best:
                    bx, by = at(x, y)
                    bits.append(f'<circle class="chosen" cx="{bx:.1f}" cy="{by:.1f}" r="4"/>')
    if label_x:
        low_x, high_x = min(xs), max(xs)
        for value in dict.fromkeys((low_x, (low_x + high_x) // 2, high_x)):
            px, _ = at(value, low)
            bits.append(
                f'<text class="tick" x="{px:.1f}" y="{height - pad_b + 16}" '
                f'text-anchor="middle">v{value}</text>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" preserveAspectRatio="xMidYMid meet">'
        + "".join(bits)
        + "</svg>"
    )


def cell(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}".rstrip("0").rstrip(".")


def verdict(tune: list[Point], holdout: list[Point]) -> tuple[str, str]:
    """The one sentence a reader wants: are the two lines still together?"""
    if not tune or not holdout:
        return "note", "只有一組文件量得到這個指標,分不出學習與過擬合。"
    gap = float(tune[-1][1]) - float(holdout[-1][1])
    if gap > DIVERGED:
        return "warn", (
            f"目前 tune 比 holdout 乾淨 {gap:.0%} —— 兩條線正在分開,"
            "這是提示詞開始學那批反覆看到的文件的形狀。回頭看兩條線還併在一起的那幾版。"
        )
    return (
        "note",
        f"目前兩條線相距 {abs(gap):.0%},仍然一起移動 —— 學到的東西還推廣得到沒看過的文件。",
    )


def ancestry(rows: list[dict[str, Any]], target: int) -> str:
    """How the chosen version was reached, back to the built-in prompt.

    Consecutive stretches are collapsed. Written out in full, a hundred rounds
    of ordinary descent is three lines of `v0 → v1 → v2 →` that hide the only
    part worth seeing: where the beam went BACK, because that is where a
    revision was bad enough to abandon.
    """
    by_version = {r["v"]: r for r in rows}
    chain, seen = [target], {target}
    while (row := by_version.get(chain[-1])) and row["parent"] is not None:
        if row["parent"] in seen:  # a cycle cannot happen, but must not hang if it does
            break
        seen.add(row["parent"])
        chain.append(row["parent"])
    chain.reverse()

    runs: list[list[int]] = [[chain[0]]]
    for version in chain[1:]:
        if version == runs[-1][-1] + 1:
            runs[-1].append(version)
        else:
            runs.append([version])
    parts = [f"v{r[0]}" if len(r) == 1 else f"v{r[0]}…v{r[-1]}" for r in runs]
    tail = "" if len(runs) > 1 else f"(連續 {len(chain)} 代)"
    return " ↩ ".join(parts) + tail


def since_best(graded: list[dict[str, Any]], best_v: int | None) -> str:
    """Whether the run has stopped finding anything better — the stop signal."""
    if best_v is None:
        return ""
    later = [r for r in graded if r["v"] > best_v]
    if not later:
        return "它是最後評分的一版,所以還不知道後面會不會更好 —— 再跑幾輪。"
    return f"在它之後又評了 {len(later)} 版,沒有一版更好 —— 迴圈已經停止進步。"


def multiples(rows: list[dict[str, Any]], xs: list[int], best: int | None) -> str:
    """One small chart per index, so nothing measured stays unlooked at."""
    blocks: list[str] = []
    for key, title, where, why in METRICS:
        if key == "clean":
            continue
        series: list[tuple[str, list[Point]]] = [("hold", series_for(rows, key, "holdout"))]
        if where == "both":
            series.append(("tune", series_for(rows, key, "tune")))
        svg = panel(series, xs, box=SMALL, best=best)
        if not svg:
            continue  # never measured in this run — silence, not an empty frame
        pair = ' <span class="pair">holdout · tune</span>' if where == "both" else ""
        blocks.append(
            f'<figure class="sm"><h3>{html.escape(title)}{pair}</h3>'
            f'<div class="chart">{svg}</div>'
            f"<figcaption>{html.escape(why)}</figcaption></figure>"
        )
    return "\n".join(blocks)


def render(rounds_dir: pathlib.Path) -> str:
    rows = load(rounds_dir)
    graded = [r for r in rows if r["holdout"]]
    if not graded:
        raise SystemExit(f"{rounds_dir} 裡沒有任何版本跑過 holdout")

    xs = [r["v"] for r in graded]
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
    held = best["holdout"] if best else {}
    page = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("TOTAL", str(len(rows))),
        ("GRADED", str(len(graded))),
        ("CHOSEN", f"v{best_v}" if best_v is not None else "—"),
        ("CHOSEN_FITNESS", f"{best['fitness']:.3f}" if best else "—"),
        ("CHOSEN_HIT", cell(held.get("lookup_hit_rate"))),
        ("CHOSEN_CLEAN", cell(clean_share(held) if best else None)),
        ("CHOSEN_JUDGE", cell(held.get("judged_keep_share"))),
        ("CHOSEN_PER_DOC", cell(held.get("mentions_per_document"))),
        ("ANCESTRY", ancestry(rows, best_v) if best_v is not None else "—"),
        ("SINCE", since_best(graded, best_v)),
        (
            "CHART_LEAD",
            panel([("hold", clean_hold), ("tune", clean_tune)], xs, box=BIG, best=best_v),
        ),
        ("MULTIPLES", multiples(graded, xs, best_v)),
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
