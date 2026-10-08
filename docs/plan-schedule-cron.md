# Plan — Cron schedules: one row for what used to take several

## Problem

A `schedules.json` row says one recurrence: every N minutes (N divides 60),
hourly, daily at a time, weekly on one day, monthly on one date. "Weekdays at
09:00" is five weekly rows; "every 2 hours" is twelve daily rows; "the 1st and
the 15th" is two monthly rows. The AI has to split them and the schedules
overview (`docs/plan-schedule-overview.md`) shows one person's one wish as five
rows, each edited on its own, each with its own history.

## Locked decisions (from /grill-me, 2026-10-08)

**user** = the user answered it; **mine** = an implementation choice listed for
overturning.

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | The existing `every` rows | **Unchanged** — same five periods, same fields, same behaviour. Cron is added beside them. | user |
| 2 | What a cron row looks like | `{"cron": "0 9 * * 1-5", "tz": "Asia/Taipei", "run": "…"}` — **standard 5 fields** (minute hour day-of-month month day-of-week; ranges, lists, steps, names like `MON-FRI`); no seconds / year fields. `tz` as today (unset = UTC). `cron` together with `every` is a refused row, said in the overview. Catch-up as today: a window the sweep missed fires once, late (the latest one); a cron row saved after a moment does not fire for it (the birth rule). | user |
| 3 | How the overview / panel describe a cron row | **The library's own words** (`cronstrue`, its zh-TW / en locales) — "在 09:00, 星期一 到 星期五" — as it writes them, not reworded ("用現有的就好了"); the cron itself on hover. A cron row is **not moved** onto the viewer's clock (its fields move together across a day boundary): in the viewer's zone it reads bare, otherwise followed by the zone's plain name — decision 3 of the polish plan's fallback. Next / last run stay relative on the viewer's clock (the backend computes the instant). | user |
| 4 | Edit time | Two modes, **簡單 / cron**. A row opens in its own kind. Cron mode: a text box, the library's words live under it, its error when the cron does not parse (Save disabled); zone in grey as today. Switching 簡單 → cron fills the equivalent cron (`30 8 * * 1`); cron → 簡單 fills the fields when the cron is one the simple form can say, else starts from defaults with a line saying the cron will be replaced. Saving writes the mode on screen — `every` fields or `cron`, the other kind's fields dropped. Switching a monthly day 29–31 to cron adds a grey line: cron skips months without that day, the simple form runs on the month's last day. | user |
| 5 | When the AI writes cron | **`every` when one `every` row says it; one `cron` row when it would take several.** Taught in `save_schedules`' description, the `author-workflow` skill and the `wui` skill's `reference.md`. The reply still says when it runs next. | user |
| 6 | Libraries | Backend: **`croniter`** (pallets-eco, MIT) to parse, validate and step occurrences in the row's zone. Frontend: **`cronstrue`** for the words, pinned to a release at least two weeks old. | mine |
| 7 | The window a cron fire claims | The occurrence itself, in the row's zone (`cron:2026-10-08T09:00`): due when the latest occurrence ≤ now is not the ledger's last window; next = the following occurrence. Identity (`trigger_id`) hashes the cron string with the other time fields, so an edit is a new schedule, as today. | mine |
| 8 | Delivery | A separate PR, based on #882 (it rewrites the same describer and modal), retargeted to master once #882 merges. | mine |

## Phases

- **P1** — backend: parse + validate (`cron` vs `every`, 5 fields, `croniter`
  refusal text), the sweep's due / next / window for cron rows, the birth rule,
  `schedule_views` (`describe`, `next_ms`), the edit route accepting `cron`.
  Parity test (route vs sweep) extended to cron rows.
- **P2** — AI: `save_schedules` description + reply, `author-workflow`,
  `wui` `reference.md` (decision 5).
- **P3** — frontend describer: cron rows through `cronstrue` (decision 3) in the
  overview and the panel.
- **P4** — Edit time's two modes (decision 4).
- **P5** — docs (`workflows.md` §22, `migrations.md` entry), review rounds, CI.
