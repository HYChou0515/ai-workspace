# Plan — make the PM app feel like GitHub Projects

The PM app already shares GitHub Projects' core model — Table / Board / Roadmap
views over typed custom fields, each record a file. This epic closes the "feel"
gaps. Four features, built one at a time (TDD, commit per phase), grilled before
each.

Branch: continues on `worktree-pm-ui-review-fixes` for now (may split into its
own PR — this is a distinct epic from the #640 review fixes).

## Locked decisions

- **View config is persisted by writing the view's `.ai.yaml`** (shared, like a
  GitHub team view). Changing group-by / filter / sort in the UI serialises back
  into the `views/*.ai.yaml` file, so the view remembers it and everyone sees it.
  (Rejected: session-local ephemeral; per-user view-state.) Implication: the write
  path must round-trip the spec safely; today's YAMLs are comment-free + simple.

## Features (proposed order)

| # | Feature | Why this order |
|---|---------|----------------|
| B | **Colored single-select chips** (status/select values shown as colored chips, GitHub-label style) | Cheapest, highest visual payoff, no persistence dependency — a quick win to validate the look |
| A | **UI "Group by ▾" in every view** (incl. collapsible groups in the Table; persisted to YAML) | The signature GitHub Projects feature; uses the locked persistence decision |
| C | **Item detail slide-over panel** (click a record → right drawer with all fields + body, without leaving the view) | Reuses the EntityFileEditor content in a drawer |
| D | **Filter / search bar** (a query bar per view, persisted to YAML) | Most complex (query syntax); do last |

## Progress

- **B — coloured chips: ✅ done.** Decided: **auto-palette + schema override**.
  A muted, on-brand categorical palette (`--cat-1..7`) lives in `tokens.css`;
  `selectColor(value, fieldSpec)` hashes a value to a stable hue, or honours a
  schema `colors:` map (`EntityFieldSpec.colors`, hue name / slot). Applied: Table
  select cells show a coloured `.ev-chip` at rest (still opens the select on
  click); Board column headers get a colour dot. Browser-verified (status +
  priority chips, schema overrides done=green / blocked=red). ⚠️ drift guard: keep
  each `--cat-*` token on its own line + don't write `var(--cat-…)` in comments,
  and build slot names from a literal array (not string interpolation).

## Open decisions (to grill per feature)

- **B — colour source:** auto-palette (deterministic per value, zero-config) vs
  schema-defined per-option colours vs both (auto default + override). Where chips
  show: Table cell (chip at rest → select on click, matching Phase 4), Board
  column headers + non-status select chips on cards.
- **A —** the group-by picker UI + how the Table renders collapsible groups; for
  the Board/Gantt `group_by` already IS the columns/lanes, so the picker rewires
  that field.
- **C —** drawer vs modal (GitHub uses a right slide-over); where it mounts; how
  it coexists with the board card ⋯ → Edit modal (Phase 3).
- **D —** query syntax (GitHub-like `assignee:@me is:open`) vs structured filter
  chips.
