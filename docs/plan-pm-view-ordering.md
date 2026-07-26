# Plan — PM view ordering + View settings panel (GitHub-Projects style)

Bring GitHub-Projects-style ordering to the PM app's Table + Board: sort by
fields (multi-level tiers), a user-defined manual drag order, and a consolidated
"View" settings gear panel. Continues [`plan-pm-ui-review.md`](plan-pm-ui-review.md)
and the #GH-projects epic (B colored chips ✅, A group-by ✅).

## Locked decisions (from grill-me)

- **D1 — sort ↔ manual interaction = GitHub model.** Manual drag order is the
  default (when no sort is applied). Applying a multi-level field sort takes over
  and **disables drag**; clearing the sort returns to the manual order. So a view
  carries two independent things: an optional ordered **sort rule list**, and the
  **manual order**.
- **D2 — manual order is shared per-project**, stored as a per-record `rank`
  (the existing `Role.RANK`) — hidden infrastructure, not a visible column. Table
  and Board both default to it; dragging in one surface updates the shared value
  so the other reflects it. Records with no `rank` fall back to record-number
  order; a new record appends (after the current max).
- **D3 — first version = the ordering block + the View gear panel.** Multi-level
  sort + manual drag (Table + Board), consolidated into a gear panel that also
  holds **Fields** (show/hide + reorder columns) and **Group by** (fold in the
  existing control). **Deferred:** Field sum (aggregations), Slice by, Show
  hierarchy (sub-issues), Roadmap tab, agent sessions.

## Persistence

- **Sort rules + Fields (hidden/order) + Group by** → serialized into the view's
  `.ai.yaml` via the existing local-apply + **"Save to view"** path
  (`AiYamlRenderer.saveView` → `fileService.writeFile` → `applyExternalWrite`,
  already per-file authoritative). Sort shape: `sort: [{ field, dir: asc|desc }]`.
- **Manual `rank`** → written on the record via the entity update path
  (`update_entity` / `write.patch`), using a fractional midpoint between the drop
  neighbours (GH-style) so a reorder rewrites ONE record, not the whole list.

## Data-model changes

- `ViewSpec` (`web/src/renderers/entity/types.ts`) + `parseViewSpec`: add
  `sort?: { field: string; dir: "asc" | "desc" }[]` and `hidden_fields?: string[]`
  (Fields show/hide already partly exists via TableView's `hidden` set + Columns
  menu — promote it into the spec so it persists).
- Issue schema (`.entity/issue/schema.yaml`): add `rank: { role: rank }`
  (hidden manual-order field). Milestones out of scope (few, roadmap-ordered).

## Phases (flat integers)

- **P1 — spec + schema.** `sort` + `hidden_fields` on `ViewSpec` + `parseViewSpec`;
  `rank` on the issue schema. Default row order = `rank` (fallback: record number).
- **P2 — multi-level sort (pure).** A stable, role-aware comparator
  `sortRows(rows, sort, type, refIndex, users)`: status by its declared value
  order, ref by resolved title, actor by name, date/number/progress natural, each
  tier asc/desc; ties fall through to the next tier, final tie = `rank`. Applied
  in `TableView` and within each `BoardView` column. Unit-tested.
- **P3 — View gear panel.** A "View" popover consolidating **Fields**
  (show/hide columns), **Group by** (move the existing `GroupByControl` in), and
  **Sort by** (add/remove tiers, pick field + asc/desc, capped at 3). Local apply
  + one **Save to view**. Retires the standalone Columns + Group-by controls.
  (Column drag-**reorder** is deferred to a follow-up — see Deferred; show/hide is
  the core Fields need.)
- **P4 — manual order (GH model).** Ordering by the shared `rank`, editable
  **only when no sort is active** (a sorted view follows the sort). Pure rank math
  (`rankForDrop` / `rankForMove` — fractional midpoint, rewrites one record).
  **Board:** drag a card onto another to reorder in front of it (writes `rank`);
  a cross-column card drop adopts the target's status too; when a sort is active a
  card drop still changes status but writes no rank. **Table:** ▲/▼ reorder
  handles per row in the plain manual view (no grouping, no sort). Board card-drag
  reorder ships here; full Table row-**drag** (vs the ▲/▼ handles) is deferred with
  column drag-reorder (see Deferred) — both need the dnd-in-table refactor.
- **P5 — persistence wiring.** `saveView` serializes `sort` + `hidden_fields` +
  `group_by` together into the view YAML; the panel drives it. `rank` writes go
  through the entity update path (optimistic + 409 like every other edit).
- **P6 — verify.** Unit: comparator (every role + multi-tier + asc/desc), panel
  interactions, rank-midpoint math, the GH-model gate (sort active ⇒ no drag).
  Real-browser (bundled chromium): sort tiers reorder rows; drag a row/card and it
  sticks + mirrors across Table/Board; panel Save persists.

## Deferred (explicit — not this version)

Field sum / aggregations · Slice by · Show hierarchy (sub-issues) · Roadmap in the
panel · agent sessions toggle · **column drag-reorder in the Fields section**
(show/hide ships in P3) · **Table row drag-reorder** (the ▲/▼ handles ship in P4;
true drag needs a dnd-in-table refactor). Tracked once the ordering block lands.

## Open points to confirm

1. `rank` on issues only (milestones keep roadmap/date order)? — assumed yes.
2. The gear panel replaces the current standalone "Columns" + "group by"
   controls (vs. sitting alongside them)? — assumed replace.
3. Multi-level sort tier cap (e.g. up to 3 tiers, like a sane UI) or unbounded? —
   assumed a small cap (3) for the editor, no hard limit in the data.
