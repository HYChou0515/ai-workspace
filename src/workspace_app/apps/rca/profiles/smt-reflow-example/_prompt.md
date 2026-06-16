## Your workspace — `smt-reflow-example` template

A worked SMT-reflow example, fully populated:

| Path | Purpose |
|---|---|
| `./brief.md` | One-page statement of the problem. Read first. |
| `./data/*.csv` | Sample fixture data — treat as if from MES / AOI. |
| `./drift.ipynb` | SPC / time-series analysis. Cells load `./data/*.csv` and plot. |
| `./pareto.ipynb` | Failure-mode ranking. |
| `./report.v1.md` | The final report (Problem statement → Findings (a/b/c/d) → Next steps; see the artifact conventions in the system prompt). New version = `./report.v{N+1}.md`. |

Suggested flow: read `./brief.md` → explore `./data/*.csv` via `read_file` → add
analysis cells to `./drift.ipynb` and `./pareto.ipynb` → draft `./report.v{N+1}.md`.
