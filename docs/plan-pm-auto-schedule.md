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
- **P2 — dropped.** The plan was to teach `rollup` to read dates, so a
  milestone's span could be derived from its issues as a compute-on-read field.
  That contradicts the decisions it was meant to serve: a rollup re-derives
  itself continuously, and #5 says nothing reschedules on its own, while #6 says
  `auto` is stored and changed by the button. So a milestone's span is computed
  by the scheduler and written, exactly like an issue's — one fewer framework
  change, and the same rule for both kinds of record. Folded into P5.
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

## Status

All phases landed except P2, which was dropped for the reason above. Verified
end to end in a running PM project: two milestones (`2026-07-01/` and
`2026-08-01/`, both ends open) and five issues.

| issue | scheduled | why |
| --- | --- | --- |
| a1 — Alice, 30 working days, M1 | 07-01 → 08-11 | from M1's start |
| a2 — Alice, 3 days, M2 | 08-12 → 08-14 | Alice is still busy, so **M2 slips** rather than clone her |
| b1 — Bob, 4 days, M1 | 07-01 → 07-06 | his own queue, in parallel with Alice |
| unowned, 2 days | 07-01 → 07-02 | the shared unassigned queue |
| Bob, no estimate | 07-07, one day, **dashed** | a placeholder to drag into shape |

The milestones' own spans followed their issues (`2026-07-01/2026-08-11` and
`2026-08-12/2026-08-14`), and every date survived a reload.

## Open

Settled while building:

- The flag is `schedule: auto | manual`, one per record — the only field either
  kind wants scheduled is its span, and `schedule: auto` says in YAML what it
  means without a second word for it.
- A milestone needs no separate anchor field. Its anchor is its span's START,
  which half-open ranges (P3) now make expressible on its own: `span:
  2026-07-01/` reads as "starts here, the end comes from my issues".
