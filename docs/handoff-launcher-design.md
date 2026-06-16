# Design handoff — App Launcher (`/`)

> For the design agent. This specifies **what** to design (the launcher screen)
> and the constraints it must honor. It does not prescribe the visual solution —
> that's your job. Decisions below are locked (from the #89 "RCA → template"
> grill); treat them as fixed requirements, not suggestions.

## Background — what changed

The platform is becoming **multi-app**. Previously the product *was* RCA: `/`
showed the investigation list. Now RCA is just **one app** produced from a
template; the platform can host several parallel apps (RCA, and e.g. a future
"FOO"), each a self-contained, separately-branded dashboard living under
`/a/:slug`.

`/` is no longer RCA's home. **`/` is an App Launcher** — the entry screen where
a user picks which app (dashboard) to enter. It is always shown, even when only
one app exists.

## What to design

The **launcher screen** only. A grid/gallery of **app cards**, each opening an
app's dashboard. Plus one **Knowledge Base card** (a peer-looking card that
links to the existing `/kb` surface — KB is not an app, it's a special link).

Out of scope: the app dashboards themselves (`/a/:slug`), the create flow, the
workspace shell, KB internals.

## Data the launcher receives

A list of **app manifest summaries** (from the backend app catalog), plus the
fixed KB card. Each app card has exactly:

| field | type | use |
|---|---|---|
| `slug` | string | route target → navigates to `/a/{slug}` |
| `title` | string | card title (the app's name, e.g. "Root Cause Analysis") |
| `description` | string | one-line card subtitle |
| `icon` | string | **one of three forms** (see below) |
| `color` | string | hex, e.g. `#c0392b` — the app's accent/theme color |

`icon` is one of:
- **inline SVG** markup (the app shipped its own `icon.svg`) — most apps will use this,
- an **emoji** (e.g. `🔥`),
- a **named icon** key from the existing `Icon` component's 44-name set (e.g. `flame`).

The card must render all three forms at a consistent size inside a consistent
icon "tile".

The **KB card** is fixed: title "Knowledge Base", links to `/kb`, uses the
existing `layers` named icon, neutral color. Make it read as a peer card but
subtly distinct (it's a different kind of destination).

## Color / theming rule (important)

Each app has its own `color`. The **full `--accent` re-theme happens after you
enter an app** (`/a/:slug` recolors `--accent` / `--accent-h` / `--accent-soft`).
On the **launcher itself**, the page chrome stays **neutral / platform-level** —
do not paint the whole launcher in one app's color. Instead, each card expresses
its own app color *locally* (e.g. icon-tile tint, a top accent bar, or a color
chip) so that several differently-colored cards coexist tastefully in one grid.
The launcher is the one place where many app colors appear at once — keep it calm.

## Constraints (must honor)

- **Reuse the existing design language.** Match the current RCA 3.0 system:
  see `design_handoff_rca_3.0/design.md` and the current `web/src/pages/Home.tsx`
  / `HomeSidebar.tsx` aesthetic (weight, spacing, typography, card feel). Do **not**
  invent a new visual language.
- **Use existing CSS tokens**, not hardcoded values: `--white`, `--paper-2`,
  `--paper-3`, `--text-paper-d`, `--text-paper-d2`, `--radius-card`,
  `--radius-btn`, and the `--accent` trio. App `color` drives the per-card accent
  (derive hover/soft locally from the single hex).
- **Use the `Icon` component** for the named-icon form (44 names available).
- Each card is a **link/button with an accessible name**; full keyboard
  navigation across the grid; visible focus ring; hover lift consistent with
  existing cards.
- **Responsive**: graceful 1 / 2 / 3-column reflow.

## States to cover

- Normal: N app cards + the KB card.
- One app only (still a launcher — don't auto-skip).
- Empty: no apps registered (only the KB card) — show a calm empty hint.
- Loading (manifests fetching).

## Open questions for you (decide and note your choice)

1. Platform-level header on the launcher — a product name/logo, account menu,
   notifications? Or a bare, centered gallery?
2. When there are many apps, do we need search/filter, or is the grid enough?
3. Card density & size — large feature cards vs compact tiles.

## Reference decisions (locked, from #89)

- Launcher always shown at `/`; apps at `/a/:slug`, items at `/a/:slug/:itemId`.
- KB is a link card, not an app.
- Identity per app = `{ slug, title, description, icon, color }`.
- Per-app color = full `--accent` re-theme **inside** the app; on the launcher,
  per-card-local only.
