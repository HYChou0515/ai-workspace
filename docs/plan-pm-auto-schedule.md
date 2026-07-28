# PM — automatic scheduling on the Timeline

The Timeline is a drawing of dates somebody typed. This turns it into a
schedule: you say how long a piece of work takes and who does it, press
**Recalculate**, and the chart lays the work out.

## What the user decided

Settled by interview, in the user's own order. These are constraints on the
design, not suggestions.

| # | Decision |
| --- | --- |
| 1 | The point is **scheduling**, not faster typing. Dates are computed. |
| 2 | An issue carries a **duration**; whether it counts **working or calendar days is set per issue**, not per project. |
| 3 | Work chains **per assignee**. One person does one thing at a time. |
| 4 | The order within a queue is the **Timeline's own order** (`rank` / the active sort). |
| 5 | Recalculation is a **button**. Nothing reschedules on its own, ever. |
| 6 | Only records flagged **`auto`** are touched. `auto` is **stored**, and means "the system may change this field". |
| 7 | A record that is not `auto` is **immovable, and occupies its assignee's time** — `auto` work routes around it. |
| 8 | A **milestone's start date** is the **lower bound** for its issues: not before this day. |
| 9 | Across milestones a person keeps **one queue**. A late milestone **slips**; nobody is scheduled to be in two places at once. |
| 10 | A milestone's own span can be `auto`: **earliest start to latest end of its issues**. |
| 11 | A new issue / milestone starts with **start = today**, everything else `auto`. |
| 12 | (Defect, raised here) A span **cannot be set on one side only** — the half-filled value is silently dropped. |
| 13 | Issues with **no assignee** share one **"unassigned" queue** — the pessimistic reading, and the honest one. |
| 14 | An issue with **no duration is still drawn**, with a default length and a **visibly different bar**, so it can be dragged into shape on the chart instead of opened one by one. |
| 15 | Dragging the **right edge changes the duration** — the scheduler's input — so `auto` stays on and the next run keeps the new length. |
| 16 | Dragging the **whole bar / left edge** writes the span and **leaves `auto` alone**: your placement is provisional and the next run may move it back. |
| 17 | **A gesture never flips a flag.** `auto` changes only when the user changes it. |

Two consequences worth stating plainly, because they are what makes the rest
coherent:

- **Length and position are different things.** For an `auto` record the length
  is what you give the scheduler and the position is what it gives back. That is
  why #15 and #16 differ, and why the right edge is safe to drag.
- **Recalculation must be reproducible.** Same data, same result — otherwise
  nobody dares press the button. This is why the anchor is a milestone's start
  date rather than "today" (#8), with today only as the fallback when no start
  is set.

## What the codebase is missing

Verified against the code, not assumed:

- **No numeric role.** The role vocabulary (`entity/schema.py`) is closed —
  `text status actor date daterange progress rank ref backref rollup` — and a
  duration is a number. This is a generic gap, not a PM one.
- **`rollup` only aggregates numbers** (`projection.py::_rollup` coerces through
  `_as_number`) and only a whole scalar field, so "earliest start of my issues"
  (#10) cannot be expressed: it must read a date, and one END of a range.
- **A half-filled range is silently discarded** (`roleWidget.tsx::DateRangeInput`
  commits only when both ends are set, or neither) and a half-open span does not
  chart (`ganttScale.spanToDates` returns null unless both ends parse) — #12.

## Phases

Flat integers, one commit each, TDD.

- **P1 — a `number` role.** The generic primitive the duration needs: schema,
  frontmatter parsing, the generated tool's arg type, form widget, table cell.
- **P2 — `rollup` reads dates and range ends.** `min`/`max` over a `date`, and
  over the `start` / `end` of a `daterange`, so a milestone's span can be
  derived from its issues (#10).
- **P3 — a span may be set on one side only** (#12): storage, the widget that
  currently drops it, and the chart, which draws a start-only span as an
  open-ended bar rather than nothing at all.
- **P4 — the PM schema.** `issue`: duration + its unit + the `auto` flag.
  `milestone`: start date + the `auto` flag. Skeletons default per #11.
- **P5 — the scheduler**, as a pure function: queues per assignee (#3, #4, #13),
  fixed work occupying time (#7), milestone lower bounds (#8, #9), the default
  length for an unestimated issue (#14).
- **P6 — the Recalculate button** and its report: what was scheduled, what was
  not and why, and what was moved back off a manual placement (#5, #16). A
  record that could not be scheduled says so on itself, through the existing
  `diagnostics` channel.
- **P7 — the unestimated bar looks unestimated** (#14).
- **P8 — the drag rules** (#15, #16, #17).
- **P9 — creation defaults** (#11).
- **P10 — verification in the running app**, not only in tests.

## Open

- The field names in P4 are the user-facing contract (they are typed into YAML
  by hand and by the agent). Proposed: `exp_days` + `exp_days_unit`
  (`working` | `calendar`) + `auto` on `issue`; `start` + `auto` on `milestone`.
- Whether `auto` should be one flag per record or per field. One flag per record
  is proposed: the only field either type wants scheduled is its span.
