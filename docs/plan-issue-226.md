# Plan — #226 可調整系統字體大小 (adjustable system font size)

> Status: grill-locked, ready for `/tdd`. FE-only.

## Problem

Users want to make the UI text bigger/smaller. Browser zoom (Ctrl + / Ctrl + wheel)
**breaks the layout, especially when enlarging** — the multi-pane IDE shell
(`GlobalLayout` is `height:100vh`; per-App workspace has px-width panes with fixed
`min-width`), and any *viewport-shrinking* zoom runs the columns out of horizontal
room. So the user explicitly wants "bigger text **without** the Ctrl+ breakage".

## Key finding (why this drives the whole design)

Typography is heavily **px-based**, not rem:

- `--text-*` tokens: **px** (`--text-body: 14px`, …) — 9 tokens.
- Inline `fontSize: <number>` in TSX: **~296** (294 app + 2 autocrud `lib/`).
- CSS `font-size: <px>` literals: **35**.

So `html { font-size }` (rem scaling) currently catches almost nothing.

Spacing/layout px (padding ~282 inline + gap ~196 + width/height ~130 + 256 css):
**left as px on purpose** — keeping spacing fixed is exactly what preserves the
multi-pane layout width when only text grows.

## Locked decisions

1. **Mechanism = font-only `rem` scaling. NOT CSS `zoom`.**
   CSS `zoom` ≡ browser zoom → reproduces the Ctrl+ layout breakage. `rem` font
   scaling does **not** change the viewport CSS-px size, so multi-column layouts
   keep their horizontal room; only text grows. Remaining risk is *local* text
   overflow in a few fixed-width chips/buttons → fixed case-by-case.
2. **Scope = all text site-wide** (~296 inline + 35 css + 9 tokens → rem).
3. **Lever = `:root { font-size: <scale*100>% }`.** Percentage respects the
   browser/OS base; rem follows. `pxToRem(n) = n/16 rem`. At scale **1.0 → 100% →
   16px root → pixel-identical to today.**
4. **Control = continuous slider 85%–150%, 5% step, default 100%**, live apply on
   drag, **debounced** persist, **% readout + reset-to-100% button**.
5. **Placement = platform-global.** A gear in `GlobalNav` (right side, near
   `HealthDot`) → global Settings modal. `GlobalLayout` renders `GlobalNav` above
   every page, so it is reachable from all Apps / KB / Diagnostics.
6. **Consolidate settings (no app-specific — apps are template-driven).**
   Global Settings hosts **Font size + Theme + Language + About (sign-in + docs)**.
   - **Remove** the per-App `SettingsModal` + settings gear from
     `pages/investigation/WorkspaceShell.tsx`.
   - **Drop** the per-App `product = manifest.title` line (already in nav/breadcrumb).
   - **Retire** the orphan `components/SettingsButton.tsx`.
7. **Migration form = `pxToRem()` helper + mechanical codemod.** Tokens + css px →
   rem. Add a **guard test** forbidding raw inline `fontSize: <number>` going forward.
8. **Monaco follows scale** — `round(base × scale)` via `editor.updateOptions`,
   re-applied when scale changes (Monaco sets explicit px, ignores root rem).
9. **Persistence = single global localStorage key** (`ui:font-scale`), applied at
   startup in `main.tsx` (`initFontScale()`) **before render** (no flash). Mirrors
   `hooks/theme.ts`.
10. **Ctrl + / − / wheel NOT intercepted in v1** (cross-browser fragile, intrusive).
    Slider only. Possible follow-up.
11. **i18n**: all new strings via `useT` (zh-TW + en). **TDD** via vitest.

## Rejected alternatives

- **CSS `zoom` / interface zoom** — reproduces the exact Ctrl+ breakage the user
  is escaping. Out.
- **Token-only rem (leave 294 inline px)** — most text wouldn't move → broken,
  inconsistent. Out.
- **Full rem incl. spacing (~1200 sites)** — font-only does NOT need spacing
  migration; keeping spacing px is what holds the layout together. Overstated; out.
- **Per-App / RCA-only placement** — violates "no app-specific". Out.

## Phases (flat integers)

- **P1 — rem foundation.** Add `pxToRem` util. Convert the 9 `--text-*` tokens and
  the 35 CSS px `font-size` literals → rem. Visually identical at 100%.
  Tests: `pxToRem`; token/css render.
- **P2 — font-scale core.** `hooks/fontScale.ts`
  (`readFontScale` / `initFontScale` / `useFontScale`) mirroring `hooks/theme.ts`;
  applies `:root { font-size: scale*100% }`; single localStorage key; startup init
  wired in `main.tsx` (no-flash). Tests: read/clamp/persist/apply + init.
- **P3 — inline codemod + guard.** Mechanically rewrite ~296 inline
  `fontSize: <number>` → `fontSize: pxToRem(n)` across `web/src` (incl. autocrud
  `lib/` 2 sites). Add guard test forbidding raw inline `fontSize: <number>`.
  Tests: guard green; spot component renders.
- **P4 — Monaco follows scale.** Read scale, pass `round(base × scale)` to Monaco
  options + `updateOptions` on change. Tests: wrapper passes scaled fontSize.
- **P5 — global Settings surface.** Gear in `GlobalNav` → Settings modal; FontSize
  slider (85–150, 5% step, readout, reset, debounce); Theme + Language + About
  consolidated in. Tests: slider behaviour, persistence, gear opens, theme/lang work.
- **P6 — remove app-local settings.** Delete WorkspaceShell `SettingsModal` + gear
  trigger + per-app product line; retire orphan `SettingsButton`; move/update
  `SettingsModal.test.tsx`. Verify no dupes; global reachable from RCA.
- **P7 — i18n + final.** Add zh-TW + en keys for all new strings. Full vitest + tsc
  + `pnpm build`; manual check across an App / KB / Diagnostics at min/default/max
  scale (no zoom-style break; spot-check fixed-width chips).
