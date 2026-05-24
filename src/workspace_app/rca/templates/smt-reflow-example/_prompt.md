## Your workspace — `smt-reflow-example` template

A worked SMT-reflow example, fully populated:

| Path | Purpose |
|---|---|
| `/brief.md` | One-page statement of the problem. Read first. |
| `/data/*.csv` | Sample fixture data — treat as if from MES / AOI. |
| `/drift.ipynb` | SPC / time-series analysis. Cells load `/data/*.csv` and plot. |
| `/pareto.ipynb` | Failure-mode ranking. |
| `/fishbone.canvas` | 6M fishbone (JSON — schema above). |
| `/5-why.md` | 5 Whys + corrective actions. |
| `/report.v1.md` | The 8D report. New version = `/report.v{N+1}.md`. |

Suggested flow: read `/brief.md` → explore `/data/*.csv` via `read_file` → add
analysis cells to `/drift.ipynb` and `/pareto.ipynb` → track causes in
`/fishbone.canvas` → narrow in `/5-why.md` → draft `/report.v{N+1}.md`.
