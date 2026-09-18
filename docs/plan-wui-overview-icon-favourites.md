# Plan — WUI overview: a page's own icon, and a viewer's favourites

Amends [`plan-wui-overview.md`](plan-wui-overview.md) (PR #811). Same PR, same
branch, phases continue at **P11** — the author's call (2026-09-18), taken
knowing it reopens a PR that four review rounds had closed.

## The ask (the author's words, 2026-09-18)

> 我希望 wui 可以提供 icon,在 wui 總覽頁面就可以預設使用圈圈加 title 的形式放上去。
> 另外我希望有我的最愛(存在 local storage)。

Two things:

1. A page may **declare an icon**; the overview row shows it. A page that
   declares none gets a **circle with the title** in it — the avatar shape.
2. A viewer can mark pages as **favourites**, kept in **localStorage**.

## Locked decisions (from /grill-me, 2026-09-18)

| Question | Decision | Rejected |
|---|---|---|
| Where does the work go? | **Into PR #811**, P11 onward. | A PR stacked on #811 (recommended; the author chose otherwise). |
| How is the icon declared? | `icon:` in `page.ai.yaml`, **the three forms App icons already take** (`AppIcon.tsx`): a file in the page's folder (`logo.png`, `icon.svg` — relative to the folder, the same rule as `entry:`), one emoji grapheme, or a named-icon key from `ICON_NAMES`. | Emoji + key only (no file); file only. |
| When is it read? | By the **server at Deploy**, into the row — the rule `title:` already follows ("read the view file at Deploy, do not trust the client", plan-wui-overview §Locked). Change it → Deploy again. | Read by the browser at render (a second reader of the view file, and a per-row file read on every listing). |
| What does the default look like? | A **circle** holding the title's first grapheme (CJK: the first character; latin: up to two initials, `UserAvatar`'s rule), tinted with **the page's App colour** (`appTagPalette`, the same tint the group heading wears). Title text beside it, as now. | A hue derived from the title (a rainbow down one group); the whole title inside a capsule (a capsule per title length). |
| A declared icon — in the circle too? | **Yes.** Emoji / named icon centred in the same circle; a file icon fills it and is clipped round. Every row's left edge is one circle of one size. | Bare icon beside a circle default (two silhouettes in one list). |
| A bad `icon:` at Deploy? | **Deploy passes.** A non-string is "none"; a file that does not load (`<img onError>`) or a key not in `ICON_NAMES` (`isIconName`) draws the default circle. `title:` is not validated either; a decoration must not block shipping a page, and the circle on the overview is the visible sign the icon did not take. | 400 with a sentence, like the other Deploy failures. |
| Where else does the icon show? | **The overview only.** The row carries the field; the reader page's favicon and the pane's tab can take it later without a schema change. | Reader-page favicon too. |
| How do favourites show? | A **"我的最愛 / Favourites" group at the top** of the overview, drawn with the same list and row as an App group. The page **stays in its App group too** — the App groups are the complete listing, the favourites group a shortcut. No favourites → no group. | Starred rows first within each App group; a "favourites only" toggle (a second piece of state to keep). |

Decisions taken by the implementer, stated so they can be overruled:

- **Scoped by the signed-in user**, inside localStorage — `onboarding.dismissed`'s
  precedent (`lib/onboarding.ts`: one JSON object, keys
  `encodeURIComponent(userId)`). Two people on one machine do not share a
  star. Still localStorage, as asked; never the server.
- **A stale favourite is kept, not pruned.** A starred page that the listing no
  longer returns (Removed, item deleted, access lost) is simply not drawn;
  its key stays. Deploying the page again — same `item_id + path`, same key —
  brings it back starred. Pruning would gain nothing and lose that.
- **Order inside the favourites group = the listing's order** (newest Deploy
  first). One rule for every group; the store is a set, not a log.
- **The star sits on the right, before Remove.** Left of the row is identity
  (circle, title, item); right is actions. ☆ unstarred, ★ starred; the
  accessible name says which way the press goes and names the page.

## Design

### The icon field

`page.ai.yaml` gains one optional key:

```yaml
view: wui
title: Lot tracker
icon: logo.png        # a file in this folder — or an emoji ("📦"), or a key ("kanban")
```

**Server (`api/wui_deploy.py`).** `DeployedWui` gains `icon: str = ""` (a
default, so rows written before P11 decode). `page_icon(doc)` beside
`page_title`: the value of `icon:` when it is a non-empty string, stripped;
anything else `""`. No file read, no key check — the decisions above. The
Deploy route sets it on the row; `DeployedPage` (the response model) carries it
through `DeployedWui.__struct_fields__` unchanged, so the overview listing needs
no edit. Writers of the row: the Deploy route, only (`pages.record`); readers:
the overview route and the FE.

**Client (`api/wui.ts`).** `DeployedWui.icon: string`.

**Rendering (`components/PageMark.tsx`, new).** One component, the row's left
edge, 28px round like `UserAvatar`:

| `icon` | Draws |
|---|---|
| matches `AppIcon`'s `FILE_ICON` (`.png .svg .jpe?g .webp .gif`) | `<img>` from the item's file route, `GET /api/a/{slug}/items/{item_id}/files/{folder}/{icon}` — the same URL `real.ts:509` spells for a file read and the same `read_content` the listing already filtered on. `object-fit: cover`, clipped by the circle. `onError` → the default. |
| one grapheme that is not `^[a-z_]+$` | the emoji, centred. |
| `isIconName(icon)` | `<Icon name>` centred, ink = the App's ink. |
| anything else, or `""` | the **default**: the title's first grapheme (`Intl.Segmenter` where present, else `[...title][0]`), or for a title whose first token is latin the initials of its first two words, uppercased — `UserAvatar`'s split. |

Fill: `appTagPalette(app.color).tint`; ink `inkLight` / `inkDark` by theme — the
palette the heading's `AppTag` already resolves, so a group's circles and its
pill agree. No App colour (a manifest without one, an App no longer registered)
→ the neutral `var(--paper-2)` `UserAvatar` uses. `aria-hidden`: the row is
named by its title link, the mark is decoration.

The file path is spelled with `encodePath` segment by segment — the CJK folder
with a space that the P2 href test pins.

### Favourites

**Store (`lib/wuiFavourites.ts`, new).** Key `rca.wuiFavourites`, one JSON
object `{ [encodeURIComponent(userId)]: string[] }`; each entry is
`` `${item_id}${path}` `` — the row's own React key, and the deploy id's two
halves. `read` / `write` in try/catch as every `rca.*` helper is; a read that
throws or parses to a non-object is `{}`. `useWuiFavourites(userId)` returns
`{ has(key), toggle(key) }` over React state, so a press re-renders the group
without a reload; the state is initialised from storage and every toggle
writes through.

**Group.** `WuiOverviewPage` builds `groups` as now, then, if any listed row's
key is in the set, prepends a group under the heading `t("wui.favourites")`
(`<h2 id="wui-favourites">`, a `<section aria-labelledby>` like the App
groups) holding those rows in listing order. Rows are drawn by the same
`PageRow`; a row in two groups is two `PageRow`s with the same `page` — the
Remove mutation is per component, and a Remove in either invalidates the one
query, so both go.

**Star.** In `PageRow`, before Remove: `<button className="btn"
data-variant="ghost" data-size="sm" aria-pressed={starred}
aria-label={starred ? t("wui.unstar", {title}) : t("wui.star", {title})}>` with
`<Icon name="star" />` — `star` is added to `ICON_NAMES` (the set says "add
new icons as the UI needs them"); the starred state fills it
(`fill="currentColor"`), the unstarred state is the stroke. `aria-pressed` is
the state; the label is the action.

### Layout (`styles/my-resources.css`)

`.wui-list`'s tracks become

```
auto  minmax(0, 1fr)  fit-content(45%)  auto  auto
mark  title           detail            star  remove
```

The new `auto` tracks are **bounded** — a 28px circle and a 28px button — so
the review-round-2 lesson ("subgrid + `auto` + free text zeroes the group") does
not apply to them; it applies to the detail track, which keeps its cap. The
narrow block (`@media (max-width: 640px)`) maps the row to
`auto minmax(0, 1fr) auto auto` — mark · title · star · remove on line one,
detail on line two spanning `2 / -1` so it stays aligned under the title, not
under the circle.

Measured, not assumed: the same Playwright harness as rounds 1–3
(`tmp/review-defect/harness.html` + `measure.cjs`) at 1280 / 700 / 641 / 390px,
with a long item title in the group — the page-title width must not fall
below what P7 recorded (322 / 289 / 256px at ≥760 / 700 / 641) by more than
the two new tracks' own width (2 × 28px + 2 gaps), and nothing may overflow at
390.

`my-resources.test.ts` pins the new column declaration through `wideRule()`
(five tracks, the two `auto`s at the ends, the cap unchanged) and the narrow
block's `grid-column: 2 / -1` for `.detail`.

### Words (`lib/i18n.tsx`)

| key | zh-TW | en |
|---|---|---|
| `wui.favourites` | 我的最愛 | Favourites |
| `wui.star` | 把「{title}」加入我的最愛 | Add “{title}” to favourites |
| `wui.unstar` | 把「{title}」從我的最愛移除 | Remove “{title}” from favourites |

### Docs

- `docs/wui.md` §發布（Deploy）/ the overview paragraph: `icon:` (three forms,
  read at Deploy, the circle default) and favourites (per viewer, per browser,
  the top group, the star).
- `sample-skills/wui/SKILL.md` "The shape": `icon:` in the `page.ai.yaml`
  example with the three forms in one comment line; `reference.md` where
  `title:` is explained.

## Deliberately not doing

- No icon on the reader page's favicon or the pane tab (decision above).
- No validation of `icon:` at Deploy (decision above).
- No server-side favourites, no sync across devices — the ask says
  localStorage. A future `UserPref` row can import the local set.
- No reordering of favourites by hand; no "starred at" timestamp.
- No favourites anywhere but the overview (not the rail menu, not the
  launcher).
- No pruning of stale favourite keys (decision above).
- No image-size or content check on a file icon — it is served by the file
  route the reader page already trusts, into a 28px box.

## Phases (one commit each)

11. **Icon — server.** `DeployedWui.icon`, `page_icon`, the Deploy route sets
    it; `DeployedPage.icon`. `docs/migrations.md`: no backfill (rows before
    P11 read `""` = the default circle).
12. **Icon — overview.** `DeployedWui.icon` on the client; `PageMark` in
    `PageRow`; the first `auto` track and the narrow mapping; the CSS guard;
    the widths measured.
13. **Favourites.** `lib/wuiFavourites.ts` + hook; the `star` icon; the star
    button; the top group; the last `auto` track; i18n.
14. **Docs + demo.** `wui.md`, `SKILL.md`, `reference.md`; the `/web-demo`
    re-recorded with a page that declares an emoji icon, one that declares
    none, and a star pressed.

## Test plan (red first, targeted only)

P11 (`tests/api/test_wui_deploy_routes.py`):
- `icon: "📦"` → the response and the listing carry `icon == "📦"`.
- `icon: logo.png` → `"logo.png"` (no file need exist).
- No `icon:` → `""`; `icon: 3` / `icon: [a]` / `icon: ""` / `icon: "  "` → `""`.
- A row stored without the field (a `DeployedWui` built the P1 way, recorded
  directly) lists with `icon == ""` — the decode default.
- Mutation: `page_icon` returning the raw value un-stripped reddens the
  `"  📦 "` case.

P12 (`WuiOverviewPage.test.tsx`, `PageMark.test.tsx`):
- No icon: the mark shows the title's first grapheme — `"出貨看板"` → `出`,
  `"Shipping board"` → `SB`, `"lot-tracker"` → `LT`; the circle carries the
  App's tint (compare against `appTagPalette` of the mocked App colour, hex —
  `reference_happydom_drops_oklch`), neutral for an unknown App.
- Emoji: the emoji, no initials. Key: an `<svg>`, no initials. An unknown
  key (`"rocket"`): the initials.
- File: an `<img>` with `src` = the encoded file URL for a CJK folder with a
  space; firing `error` on it swaps in the initials.
- `aria-hidden` on the mark; the row's accessible name is still the title.

P13 (`wuiFavourites.test.ts`, `WuiOverviewPage.test.tsx`):
- `read` of nothing / garbage / a non-object → empty; `toggle` twice is a
  no-op on storage; two users' sets do not mix; a user id containing `:`
  and one containing `%` stay distinct.
- The page: no stars → no favourites group; star one row → the group appears
  first, holds that row, the row is still under its App; star a second in
  another App → the group lists both in the listing's order; unstar → the
  group goes when empty. A starred key the listing does not return draws
  nothing and survives in storage. `aria-pressed` flips; the label names the
  page and the direction. Remove from the favourites group removes the row
  from both (one DELETE).
- Guards: the star button pins `.btn` + `data-variant` (round 4's lesson);
  the favourites heading is `t("wui.favourites")`, asserted literally in
  zh-TW (`translate("zh-TW", …)` — the runner's `navigator` is not the
  locale, `reference_ci_node_locale_differs`).
- CSS: five tracks declared once in the wide rule; `.detail` spans `2 / -1`
  in the narrow block; deleting either reddens.

P14: `mkdocs build --strict` green; the demo's frames checked for the circle,
an emoji mark, and a filled star.

## Verified ground truth (file pointers, branch `worktree-wui-overview` @ `46f7c692`)

- `src/workspace_app/api/wui_deploy.py:52` `class DeployedWui(Struct)` — six
  fields, `title` at :61. `:120` `page_title(doc, path)`. `:138`
  `class DeployedPage(BaseModel)`. `:162` the Deploy route; `:206–214` builds
  the row (`title=page_title(doc, path)` at :210). `:242` `GET /wui`, which
  copies every `DeployedWui.__struct_fields__` into `DeployedPage` (:266) — a
  new struct field flows through without an edit there.
- `web/src/api/wui.ts:28` `type DeployedWui`; `:24` `wuiAddress` (uses
  `encodePath` from `api/refPath.ts:53`).
- `web/src/api/real.ts:509` — the file-read URL:
  `${API_PREFIX}/a/${slug}/items/${itemId}/files/${encodePath(path)}`.
  Backend: `api/file_routes.py:734` `GET …/files/{path:path}` returns raw
  bytes with a guessed media type (an `<img src>` works; `read_content`).
- `web/src/pages/WuiOverviewPage.tsx:70–75` groups by `slug` in first-seen
  order; `:96–104` the `<section aria-labelledby>` + `<h2><AppTag/></h2>` +
  `<ul className="wui-list">`; `:118` `PageRow` — title `<a>`, `.detail`
  (item link · by/when), Remove button (`.btn` `data-variant="secondary"`
  `data-size="sm"`, :152–158), `.error`.
- `web/src/components/AppIcon.tsx:26` `FILE_ICON`; `:28` the three forms in
  order file → emoji (`icon.length <= 2 && !/^[a-z_]+$/`) → `Icon`.
  `Icon.tsx:9` `ICON_NAMES` (no `star`); `:22` `isIconName`.
- `web/src/components/UserChip.tsx:4` `UserAvatar` — the initials rule
  (`split(/[\s_-]+/)`, first letter of up to two tokens, `"?"` fallback) and
  the circle (`borderRadius: 50%`, `var(--paper-2)`, `fontSize: size * 0.43`).
- `web/src/components/AppTag.tsx:42` `appTagPalette(app?.color)`;
  `lib/appColor.ts:53` returns `{ tint, inkLight, inkDark } | null`.
- `web/src/lib/onboarding.ts:6–27` — the user-scoped localStorage store
  (`KEY`, `read` with try/catch → `{}`, `scopeKey = encodeURIComponent(userId)
  + ":" + scope`). `lib/wuiAutoBuild.ts:27` `KEY = "rca.wuiAutoBuild"` — the
  `rca.*` naming and the try/catch on both sides.
- `web/src/hooks/useCurrentUser.ts:41` `useCurrentUser(): string`.
- `web/src/styles/my-resources.css:402–415` `.page .wui-list` —
  `grid-template-columns: minmax(0, 1fr) fit-content(45%) auto`; `:417–422`
  `> li` subgrid; `:424–431` `.detail` wraps; `:527–550` the narrow block
  (`> li` → `minmax(0, 1fr) auto`, `> li > a` column 1 row 1, `> li > button`
  column 2 row 1, `.detail` on row 2). `.live-list` (:194–204) already leads
  with a bounded `auto` for its dot — the shape the mark track copies.
- `web/src/styles/my-resources.test.ts` — `wideRule()` reads the sheet with
  every `@media` block stripped (P8).
- `sample-skills/wui/SKILL.md:183–204` "The shape" — the `page.ai.yaml`
  example (`view`, `title`, a commented `tools`), "`title` is what the pane is
  called. `entry` overrides `index.html`". `reference.md:130` the next
  `title:` example.
- `docs/wui.md:226–245` the overview paragraph (only Deployed pages; the title
  rule; Remove).
- Tests today: `tests/api/test_wui_deploy_routes.py` (20),
  `WuiOverviewPage.test.tsx` (11), `my-resources.test.ts` (10).

## Risks

- **Two `auto` tracks on a subgrid list** — bounded content, so the round-2
  collapse should not recur; but "should not" is what round 2 also believed.
  Measure at the four widths before P12 is called done; the numbers go in the
  commit message with the harness path.
- **`Intl.Segmenter`** is absent in happy-dom / older Node: the fallback
  `[...title][0]` is what the tests exercise; a browser with the segmenter
  gives the same answer for every title the tests use. A ZWJ-joined emoji
  title would differ (one grapheme vs its first code point) — noted, not
  handled.
- **A file icon is a per-row image request** on the overview (one per Deployed
  page that declares a file). The route is cheap and cached by the browser;
  a listing of hundreds of file-iconed pages is not this deployment's shape.
- **The favourites group draws a second `PageRow` for the same page.** Two
  Remove buttons for one row is by design (both work, one press suffices);
  two `aria-pressed` stars for one page must flip together — they read one
  state, so they do, and a test pins it.
