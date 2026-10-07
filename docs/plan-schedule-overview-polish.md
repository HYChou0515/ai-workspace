# Plan — Schedules overview polish: read in your own clock, named in your own words

## Problem

`/schedules` shipped in #879 (`docs/plan-schedule-overview.md`). Recording it
end to end (the `/web-demo` tour, 2026-10-07) and walking the video frame by
frame turned up 25 things a person cannot read or cannot act on. The worst:
one table mixed `UTC` and `Asia/Taipei`, labelled each time with an IANA id,
so "when does this run" had to be computed in the reader's head. The rest are
names nobody chose (a workflow's id instead of its title, a page's folder
instead of its title), a table that splits into misaligned tables when grouped,
feedback that never clears and cannot be followed, and a schedule's
conversation that does not say what it is.

## Locked decisions (from /grill-me, 2026-10-08)

**user** = the user answered it; **mine** = an implementation choice listed for
overturning.

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | Which zone does the page speak? | **The viewer's** — the browser's zone. Next run, last run AND the period are converted; no zone label anywhere in the table. | user |
| 2 | How is an instant written? | **Relative where it helps**: "15 分鐘後" / "3 分鐘前" within the hour, "今天 HH:MM" / "明天 HH:MM" / "昨天 HH:MM", else "M/D(週X) HH:MM" (with the year when it is not this year). The full date and time on hover. 24-hour everywhere. | user (convert) / mine (phrasing) |
| 3 | How is a period converted? | Through a real occurrence (the row's next run, else one computed for today): daily 09:00 UTC reads "每天 17:00" in Taipei; a weekly that crosses midnight shifts its weekday. A period that does not convert cleanly — a monthly whose occurrence lands on another day of the month — keeps the time as written, followed by the zone's **plain name** (`Intl` `longGeneric`, e.g. 世界標準時間), never an IANA id. The hover says what was set ("設定為：每天 09:00（世界標準時間）"). | user |
| 4 | A row with no `tz` | **Is UTC** — that is what the sweep fires it by — and is converted like any UTC row. | user (corrected) |
| 5 | Minutes / hourly | Never labelled with a zone (they read the same everywhere). `n: 1` reads "每分鐘". | user (labels) / mine (`n: 1`) |
| 6 | Edit time | 24-hour hour + minute selects (no AM/PM). The form opens on the row **converted into the viewer's zone** when it converts cleanly (else as written, in its own zone); the zone is a dropdown of plain names, defaulting to the viewer's; it saves in the zone chosen. | user |
| 7 | Server-side "user's zone" | **Dropped.** No profile carries a zone today; neither the scheduler, the AI nor the page stamps one. | user |
| 8 | Run now vs scheduled | The last-run cell shows a **"手動" tag** when that run was started by Run now. Needs a new field on `WorkflowRun`; runs from before it read as scheduled. | user |
| 9 | Arriving from "last run" | The schedule's conversation shows a **banner** — "這是排程「<workflow title>」的執行紀錄" — with a **"回到排程"** link, and the run's progress opens **expanded**. | user |
| 10 | Names | Where = the item's title, and for a page's schedule the page's **title** (its Deployed view's `title`, else the folder). Workflow = the workflow's **title** (the item's own file, else the profile's manifest), its id only when it has none. | mine |
| 11 | Grouping | **One table**; grouping inserts a heading row per App, so columns stay aligned. | mine |
| 12 | Nothing needs attention | Sorting by "needs attention" when no row does says so in one line instead of looking like a dead control. | mine |
| 13 | Feedback after Run now | "已開始執行 · 查看" links to the schedule's conversation, and goes away once the row's last run IS that run (the last-run cell then shows it). The page refreshes itself — every few seconds while a run on it is going, every minute otherwise — and relative times move with the clock. | mine |
| 14 | Remove | Danger-coloured button; the confirm names the item / page, the workflow title and the period as this page shows it; **Cancel first**. | mine |
| 15 | Focus on opening Edit time | On the dialog, not the first field — the field's focus ring is the accent (red) and read as an error. | mine |
| 16 | Scope | Items 26–28 of the findings (welcome modal, nav labels, item-page chrome in English) are **not in this plan** — they predate #879 and are not the overview. Item 25 ("第 1 步 · p") is a phase with no title, the author's choice (`phaseView` falls back to the id); the demo uses titled phases. | user |

## Design

### 1. Time in the viewer's clock — `web/src/lib/scheduleTime.ts`

One FE module; the backend keeps answering in instants (`next_ms`,
`last_run.started/ended`) and rows as written (`raw`). Pure functions over
`Intl`, `now` passed in so tests pin it:

- `whenText(ms, now, t)` → the relative phrasing (decision 2); `fullTime(ms)`
  for the hover.
- `zonedMs(date, "HH:MM", zone)` → the instant of a wall time in a zone (offset
  probe, iterated once for DST edges), and `wallOf(ms, zone)` → `{y, m, d,
  dow, hh, mm}`.
- `periodText(raw, nextMs, now, t)` → the period in the viewer's zone (decision
  3) plus `setText` for the hover. Minutes/hourly have no zone. A row whose
  `tz` the browser cannot resolve falls back to "as written" with the id — the
  backend has already marked that row refused.

`describeSchedule` becomes this (the item panel and the overview still share
one describer, so one row cannot read two ways); the panel's "下次" uses
`whenText` too.

### 2. Names from the backend

`ScheduleView` gains `run_title`; `grade_file` already reads each distinct
workflow file once for `unparsable_workflow` — the same read yields its title
(`workflow_title_and_problem`), so no extra round trip. A profile workflow's
title comes from `profile_workflows`. `OverviewRow` gains `page_title`, from the
Deployed page already looked up for `page_path`.

### 3. "手動" — `WorkflowRun.by_hand`

`by_hand: bool = False` on `WorkflowRun` (additive msgspec field; old rows
decode as `False` = scheduled, which is what they were unless pressed — there
is no way to tell, and the decision accepts that). `orchestrator.start(...,
by_hand=)` → `start_page_schedule(..., by_hand=)` → the Run-now route passes
`True`. `LastRun.by_hand` carries it to the page.

### 4. The schedule's conversation

The overview's last-run link is `?chat=<trigger_id>&from=schedules`. The item
shell, when `from=schedules` names the chat it is showing, renders the banner
(the chat's workflow title from the run / item workflows) with a link back to
`/schedules`, and passes `defaultExpanded` to `WorkflowProgress` — an override
for that mount only, never written to the remembered preference.

## Phases

- **P1** — backend: `run_title`, `page_title`, `WorkflowRun.by_hand` +
  `LastRun.by_hand`; tests red first.
- **P2** — `scheduleTime.ts` + `describeSchedule` in the viewer's clock; the
  panel's next run; tests pinned at fixed `now` / zones.
- **P3** — overview table: names, one table with group rows, `每分鐘`, labels,
  "needs attention" note, Remove styling, 手動 tag, Run-now link that clears,
  refresh cadence + clock tick.
- **P4** — Edit time (24h selects, zone dropdown, open converted, initial
  focus) and the Remove confirm (wording, Cancel first) — overview and panel.
- **P5** — the schedule conversation's banner + expanded progress.
- **P6** — docs (`migrations.md` entry, `workflows.md`), live `/web-demo`
  re-recording, each of the 25 findings checked against the new video.
