# Plan — Schedules overview: one page that lists every schedule you can see

## Problem

A schedule lives in the item that declared it — the item's own
`.workflows/schedules.json`, or a WUI page's `<folder>/schedules.json` — and the
only place that shows one is that item's Workflows panel, which shows the item
file only (`GET /a/{slug}/items/{item_id}/schedules` reads `ITEM_SCHEDULES_PATH`
and nothing else, `api/workflow_routes.py:217`). Page-level schedules appear
nowhere in the UI. Nobody can answer "what is on a clock, when did it last run,
did it work, when does it run next" without opening items one by one; and the
panel offers no way to move a schedule's time or run it now.

## Locked decisions (from /grill-me, 2026-10-07)

Each row says who decided it: **user** = the user answered it; **mine** = an
implementation choice I made and listed for overturning.

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | Which "scheduler"? | **Item / page schedules** (`schedules.json`), as a product page like `/wui`. Not profile `triggers.json`, not the lifecycle sweepers. | user |
| 2 | Who sees what? | **`read_meta` per item, denied → skipped** — the gate of `/wui` and of the per-item schedules route. No admin "see everything". | user |
| 3 | Columns that matter | **Last run time + last run status, and next run.** | user |
| 4 | "Last run" when no run exists | **Has a run → show it; none → "never run".** Nothing extra is recorded for a window that did not start (start errors stay in the log; an overrun shows as the previous run still running / awaiting review). | user |
| 5 | Row actions | **Open**, **Remove**, **Edit time**, **Run now**. | user |
| 6 | Run now | The row's workflow + payload, in **the schedule's own chat** (so it becomes "last run"); **as the presser**; **does not touch the ledger** (next scheduled time unchanged); refused with the reason while the previous run is still active. | user |
| 7 | Run now's gate | **`execute`** — the verb of the other payload-carrying run (`wui/run`) and of the schedule's "run as me" binding. The panel's Run uses `converse` but carries no payload. | mine |
| 8 | Edit time | **Edit the time fields of an existing row only** (`every`, `n`, `at`, `dow`, `dom`, `tz`); `run` and `payload` are not editable; **no "add"** anywhere in the UI. In the overview **and** the item panel (one implementation). Validated by the sweep's own linter before writing. Gate `edit_content` (that of `save_schedules`). | user (scope) / mine (gate) |
| 9 | Edit/Remove/Run in the panel too | Every action exists in both places, one implementation, so the two entrances never differ in what they can do. | mine (stated, not objected) |
| 10 | Catch-up after an edit | **No catch-up.** | user |
| 11 | Mechanism | **A schedule identity only fires windows whose target is after it first landed** (see Design §1). Lives in the sweep, so every door obeys it. | user (approved after a worked example) |
| 12 | Applies to new rows too? | **Yes.** A daily 09:00 saved at 14:00 first runs tomorrow 09:00, not within the minute. Behaviour change → `docs/migrations.md`. | user |
| 13 | Layout | One table. Columns: item (+ page folder) / workflow / period / next / last (time + status, or "never run") / actions. Rows that will not run show the reason in red in the "next" column. | mine (proposal), user (extended by 14) |
| 14 | Grouping and sort | **Switchable on the page**: group none / by app; sort by next run / failures first (`failed`, `cancelled`, `awaiting_human` first). Default none + next run; the choice remembered per viewer in `localStorage`. App filter dropdown. | user (switchable) / mine (defaults) |
| 15 | Entry | Global destination **Schedules**, beside WUI, visible to everyone; empty state explains. | mine (stated, not objected) |
| 16 | History and "run as me" after an edit | **Not carried over.** An edit makes a new identity (`trigger_id` hashes the time, `user_schedules.py:368`): its chat is new, "last run" restarts at "never run", a run-as-me binding must be pressed again. The edit form says so in one sentence before saving. | mine (recommended, not explicitly confirmed — overturnable) |

## Design

### 1. Birth rule — no window from before a schedule existed

Today `is_due` = "this period's target has passed and the ledger has not fired
this window". A schedule identity with no ledger row therefore fires the
current window at once, whether it existed at 09:00 and the sweep was down, or
was written at 14:00. The missing fact is **when the row appeared**.

- **Record it at the one door every write shares.** `_note_schedule_file`
  (`api/app.py` ~1191) runs on every landing of a `schedules.json` — facade
  writes (file PUT, `write_file`, `save_schedules`, the new edit route) and the
  mirror's upload of `exec` writes. It now also stamps
  `_ScheduleIndex.landed_at[path] = now_ms` (additive field, default `{}`; no
  migration — a missing stamp means "unknown", which keeps today's behaviour).
- **Apply it in the sweep.** For an identity with **no ledger row**, if this
  period's target ≤ `landed_at[path]`, claim the current window without firing
  (the ledger now says "handled"); the next period fires normally. If the
  target is after the landing, it is a window the schedule was alive for — the
  existing catch-up fires it, unchanged.
- **Same rule in every reader of "next run".** `schedule_views` (route, tool
  reply, overview) takes the stamp so a row born after its target reports the
  next period, not "next sweep". The existing parity test
  (`tests/api/test_schedules_route_parity.py`) is extended so the rows marked
  due here are the rows the sweep fires.
- Profile `triggers.json` is untouched: operator config, static, not born by a
  write.

Known gaps (rare; both lose one run, never add one):
- Host-managed deploys index an `exec`-written file at turn end
  (`schedule_reconcile`), so its stamp is the turn's end, not the write.
- An old identity that has never fired, whose file is re-saved for another row
  after this period's target while the sweep was also down, is treated as new
  for that period.

### 2. Listing — `GET /schedules`

`ScheduleIndex.items_with_paths()` → per item `locator.require_access(slug,
item, "read_meta")` (memoised per item, denied → skip, as `wui_overview`
does) → read each path through the facade (as the per-item route does) →
grade with the same code the per-item route uses. That grading sequence
(parse → cap → offered → unparsable → index → ledger) is today inline in both
`list_item_schedules` and `save_schedules_impl`; it is extracted once and used
by all three.

- `last_window_lookup` hardcodes the item folder (`user_schedules.py:621`); it
  takes the path, as `schedule_binding_routes._keys` already does.
- Each row carries its `trigger_id`, and **last run**: `Conversation(trigger_id)
  .run_id` → `WorkflowRun` → `status`, `started`, `ended` (two point reads; no
  run → "never run").
- `can_edit` (`edit_content`) and `can_run` (`execute`) per item, so the FE
  hides what the viewer may not press.

### 3. Actions — keyed by `(item, path, trigger_id)`

All three re-read the file on the server and find the row by its `trigger_id`;
a row that is no longer there is a 409 ("this schedule changed — reload"),
never a write against stale bytes. The panel's current FE-side rewrite for
Remove moves onto the same route.

- `POST .../schedules/edit` — new time fields → validated with the sweep's
  linter → file rewritten with that row replaced (other rows byte-preserved,
  including ones the linter refuses).
- `POST .../schedules/remove` — the row dropped.
- `POST .../schedules/run` — `chat_for_schedule(item, workflow, trigger_id)`
  then `orchestrator.start(captured_user=presser, env_user=presser,
  payload=row.payload, chat_id=…)`; `ActiveRunExists` → 409 with the reason.
  The ledger is not written.

### 4. FE

- `/schedules` page, destination in `usePlatformDestinations.ts` beside WUI.
- Table per decision 13; toggles per 14 (`localStorage` behind try/catch).
- Open: the item `/a/{slug}/{item}`, a page row's folder `/w/{slug}/{item}/…`;
  the last-run cell opens the schedule's chat — which needs a deep link: the
  item route gains `?chat=<id>` (`ItemChatShell` picks it as `activeChatId`).
- Edit-time form: fields switch by `every`; one sentence per decision 16.
- The Workflows panel's schedule rows gain Edit time and Run now through the
  same components.

### 5. Wording that changes with decision 12

`workflow/triggers.py:371` (the `next_run` docstring states the old user
behaviour), `workflow/user_schedules.py:484`, `docs/workflows.md:888`,
`sample-skills/wui/reference.md:312`, and the `save_schedules` tool reply.

## Phases

- **P1** Birth rule: `landed_at` stamp, sweep claim-without-fire, `schedule_views` + parity test.
- **P2** Grading extracted once; `last_window_lookup` per path; `GET /schedules` with last run.
- **P3** Edit / Remove / Run-now routes (keyed by `trigger_id`); panel Remove moved onto them.
- **P4** FE overview page, destination, toggles, chat deep link.
- **P5** Panel: Edit time + Run now.
- **P6** Wording (§5), `docs/migrations.md` entry, `docs/workflows.md` §22.11.
- **P7** Live check in a fresh worktree: create / edit / run-now / remove, and a row saved after its target that does not fire.

## As built (2026-10-07) — where the code differs from the design above

Written after the phases, each line checked against the code it names.

- **Statuses.** A run's states are `pending` / `running` / `awaiting_human` /
  `done` / `error` / `cancelled` (`workflow/run.py:RunStatus`); there is no
  `failed`. "Needs attention first" (decision 14) puts `error`, `cancelled` and
  `awaiting_human` first — labelled 「需要處理的在前」, since waiting for a
  review is not a failure.
- **§2, "used by all three" is two.** `grade_file` serves the item route and the
  overview. `save_schedules` keeps its own sequence: it *refuses* before it
  writes (unknown `run`, a workflow that will not parse, over the cap), while
  `grade_file` *describes* a file already written; it reads the landing stamp
  the same way for its "next run" sentence.
- **§3, Remove also finds a row by its value.** A row the sweep refuses has no
  identity (`trigger_id: ""`), and the panel could remove those before. So
  `RowRef.raw` — Remove only — finds the first row equal to it as JSON (object
  key order ignored, list order kept: the old panel's `sameJson` rule). The
  rules the panel used to hold itself moved to the server with their tests
  (P5), and the overview offers Remove on refused rows too. The item route now
  returns `can_edit` / `can_run` so the panel offers only what is allowed.
- **§4, Open on a page row.** `WuiPage` opens a *view file*, not a folder, so a
  page row opens the **Deployed** page in its folder (`page_path`, from the WUI
  overview's own store); a page never Deployed opens the item.
- **§4, sorting across zones.** `next_at` is each row's wall clock; rows carry
  `next_ms` (one instant) and the page sorts on that.
- **§4, the deep link** is read from `window.location` inside `ItemChatShell`
  (its tests mount no router); an id the item has no chat for falls back to the
  most recent chat.
- **Copy that had to change** beyond §5: `sample-skills/author-workflow/SKILL.md`
  told the agent "a row whose time has already passed today runs on the next
  sweep" — the opposite of decision 12.
