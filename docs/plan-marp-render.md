# Plan — Render Marp decks in the workspace file preview

> A `.md` file whose YAML frontmatter carries `marp: true` renders as a **slide
> deck** in the workspace IDE preview — scroll the whole stack, or hit one button
> to present full-screen. Faithful Marp via the official `@marp-team/marp-core`.
> **Front-end only** — no backend, no change to `make_deck`.

Grilled (`/grill-me`) and locked. This is a new **file renderer** plugged into
the existing preview registry, not a new tool and not a presentation *authoring*
feature.

## Motivation

The app already renders many file types in the workspace preview (`registry.ts`),
and a plain `.md` renders as flowing prose via **react-markdown**. But a `.md`
that is actually a Marp deck (frontmatter `marp: true`, slides separated by `---`,
theme/pagination directives) renders as one long wall of text with stray `---`
rules — the slides are invisible. Marp markdown is also far easier for an agent
to author than the `pptxgenjs` program `make_deck` drives, so "the agent writes a
`deck.md`, you see slides" is a natural, cheap capability — **once the preview can
render it**. This plan adds exactly that: the render half.

## Non-goals (explicitly out of scope)

- **Not** authoring: no new tool/skill to *generate* Marp; the agent already
  writes files with `write_file`. (Grill Q4 → front-end renderer only.)
- **Not** a replacement for `make_deck` / the `pptxgenjs` deck loop (#284).
- **Not** the KB document viewer (`KbDocBody`); workspace preview only. A Marp
  `.md` opened in a KB collection keeps rendering as plain markdown for now.
  (Grill Q5.)
- **Not** custom user themes (registering extra `.css` theme sets). The three
  built-in themes (`default` / `gaia` / `uncover`) ship with `marp-core` and work
  via the `theme:` directive; custom theme registration is a follow-up.
- **Not** the marp browser runtime script — auto-scaling "fitting" headers won't
  JS-fit (see Risks); everything else renders faithfully.

## Locked decisions (from the grill)

1. **Detection — frontmatter `marp: true`.** Faithful to the Marp ecosystem; a
   real Marp file just works, zero rename. The preview registry `match(path)`
   only sees the *path*, not the body, so detection cannot live in the registry —
   it lives **inside** the markdown render path (branch after the buffer text is
   read). Non-Marp `.md` is completely unchanged.
2. **Engine — `@marp-team/marp-core`.** The official full Marp: built-in themes,
   directives (`paginate`, `_class`, `backgroundImage`), math (KaTeX), emoji, fit
   auto-scaling. `marp.render(md) → { html, css }`. Chosen over the lower-level
   `marpit` (no built-in themes) and over a hand-rolled `react-markdown` split
   (not really Marp). Cost: it bundles its own `markdown-it` stack alongside the
   app's existing `react-markdown` (remark/micromark) — two markdown engines in
   the bundle, accepted for fidelity.
3. **Presentation — scroll-stack + a full-screen "Present" button.** Default view
   scrolls the whole deck top-to-bottom (marp's HTML is already stacked
   `<div class="marpit-slide">` blocks, one `<section>` per slide). A button
   enters the Fullscreen API and presents one slide at a time (← → to move, Esc
   to leave). (Grill Q3.)
4. **Scope — front-end renderer only.** No backend, no `make_deck` change. (Q4)
5. **Applies to — the workspace file preview only.** (Q5)
6. **Isolation — shadow DOM + DOMPurify.** Marp's theme CSS is global-ish
   (`* {}`, `section {}`, `:root`), so it is injected into a **shadow root**
   (`<style>{css}</style>` + sanitized html) — isolated from the app both ways,
   and deliberately *not* inheriting the app's light/dark tokens (a slide should
   look like its slide theme). `dompurify` (already a dependency) sanitizes the
   html before injection. Image `src` and CSS `background-image: url()` that point
   at **workspace-relative** paths are rewritten through `svc.fileUrl(src, path)`
   — the same resolver `MarkdownRenderer` already uses for `![](./x.png)`.

## How it plugs in (integration point)

`MarkdownRenderer` (`web/src/renderers/MarkdownRenderer.tsx`) is the single owner
of the `.md` render path and already:

- reads content + edits via `useFileBuffer(path)`,
- gets the Edit/Preview state from `useEditMode()` (the toggle lives in the tab
  strip, VSCode-style — **not** a per-renderer button), showing Monaco when
  editing and react-markdown when previewing,
- resolves workspace-relative image `src`/link `href` via `svc.fileUrl(src, path)`.

So the change is surgical: **when previewing (not editing) and the frontmatter
says `marp: true`, delegate to `<MarpDeck text path />`; otherwise the current
react-markdown path.** Editing still opens raw Monaco for both. No registry entry
changes, no new file type, and every other `.md` behaves exactly as today.

## Rendering pipeline (inside `MarpDeck`)

```
text (marp markdown)
  │  new Marp({ script: false }).render(text)        ① engine
  ▼
{ html, css }
  │  rewriteMarpAssets(html, css, (src) => svc.fileUrl(src, path))   ② workspace images/bg
  ▼
{ html', css' }
  │  DOMPurify.sanitize(html', { allow marpit/section/svg structure })  ③ sanitize
  ▼
shadow root:  <style>{css'}</style> + html'          ④ inject, isolated
  │  measure pane width → transform: scale(paneW/1280) per .marpit-slide  ⑤ fit width
  ▼
scroll-stack of slides   (+ "Present" → Fullscreen API overlay)
```

- **① engine** — `script: false` so no `<script>` is emitted (it would be stripped
  by DOMPurify anyway; disabling it keeps the output clean).
- **② asset rewrite** — a pure function: rewrite `<img src>` and
  `style="...background-image:url(...)..."` / theme `background-image` whose target
  is a workspace-relative path (not `http(s):`/`data:`/`#`) to `svc.fileUrl`.
- **④ shadow root** — `marp-core` slides are fixed **1280×720** boxes; the shadow
  root keeps that CSS off the app.
- **⑤ scale-to-fit** — compute `scale = paneWidth / 1280` and `transform: scale`
  each slide (marp's own bespoke/bare templates scale the same way), re-measuring
  on resize via `ResizeObserver` (same primitive the Gantt view uses).

## Known risks / chosen defaults

| Risk | Decision |
| --- | --- |
| marp emits a `<script>` for JS auto-scaling ("fit" headers); it can't run inside a DOMPurify'd shadow root | `new Marp({ script: false })`; accept fit-headers don't JS-shrink in v1. Everything else (themes, layout, pagination, math, images) is faithful. |
| Emoji render as `<img>` pointing at the **twemoji CDN** | Offline/air-gap → emoji glyphs won't load (text still shows). Noted, not blocked; the app's own assets stay self-hosted. |
| Two markdown engines bundled (`markdown-it` for marp + `react-markdown` for prose) | Accepted for Marp fidelity (locked decision 2). |
| `marp-core` render may be heavy / not fully supported under happy-dom in unit tests | Test the pure helpers (detect, asset-rewrite, scale math) directly; for the component, if happy-dom chokes on the real engine, inject a fake `render` that returns a known `{ html, css }` and assert the sanitize→shadow→rewrite→scale wiring. Verify the *real* engine in the P5 web-demo (per `feedback_llm_features_need_live_checks`: a fake ≠ works). |
| Our own CSS drift | Container + Present button use tokens only, no hex. Marp's theme hex lives **inside** third-party rendered content in the shadow DOM — not our stylesheet, so not drift. |

## Phases (flat integers; each a red→green→refactor TDD loop, one commit each)

- **P1 — pure helpers.** `web/src/renderers/entity/…` or `web/src/renderers/marp/marpDeck.ts`:
  - `isMarpDoc(text): boolean` — parse the leading `---…---` YAML frontmatter,
    true iff `marp: true`. (Edge cases: no frontmatter, `marp: false`, frontmatter
    not at byte 0, CRLF.)
  - `rewriteMarpAssets(html, css, resolve): { html, css }` — rewrite workspace-
    relative `<img src>` and `background-image:url()`; pass through
    `http(s)`/`data:`/absolute-api/`#` untouched.
  - `slideScale(paneWidth, slideWidth=1280): number`.
  - Tests: `marpDeck.test.ts`.
- **P2 — `MarpDeck.tsx`.** Component `{ text, path }`: run `marp-core`, rewrite
  assets, sanitize, inject into a shadow root, scale to measured width, render the
  scroll-stack. Tests: `MarpDeck.test.tsx` (fake-render strategy per Risks) — a
  deck of N `---`-separated slides yields N slide containers; css injected; a
  `![](./x.png)` src is rewritten to the file API; a `http://` image is untouched.
- **P3 — wire into `MarkdownRenderer`.** Preview + `isMarpDoc(text)` →
  `<MarpDeck>`, else react-markdown; editing → Monaco unchanged; non-Marp `.md`
  unchanged. Tests: extend `MarkdownRenderer` test — a `marp: true` buffer shows
  the deck, a plain `.md` shows prose, editing shows Monaco for both.
- **P4 — Present (fullscreen).** A "Present" control on the deck → Fullscreen API
  on an overlay that shows one slide at a time; ← → step, Esc exits, wraps clamp
  at ends. Tests: `MarpDeck.test.tsx` — button requests fullscreen (mock the API),
  arrow keys change the active slide index, Esc restores scroll view.
- **P5 — deps + real demo.** Add `@marp-team/marp-core` to `web/package.json`
  (`dompurify` already present); `pnpm install`; `pnpm run typecheck` + full
  vitest green; run the real app and **web-demo** a `deck.md` rendering as slides
  + Present mode (GIF, per the running-worktree recipe used for the Gantt work).

## Files touched (anticipated)

- `web/src/renderers/marp/marpDeck.ts` — new, pure helpers.
- `web/src/renderers/marp/MarpDeck.tsx` — new, the deck component.
- `web/src/renderers/MarkdownRenderer.tsx` — branch to `MarpDeck` when Marp.
- `web/src/styles/entity-views.css` (or a new `marp.css`) — container + Present
  button styling (tokens only).
- `web/package.json` — add `@marp-team/marp-core`.
- Tests: `marpDeck.test.ts`, `MarpDeck.test.tsx`, `MarkdownRenderer` test additions.

## Verification / DoD

- Full FE vitest suite green; `pnpm run typecheck` clean.
- A `deck.md` with `marp: true` renders as a scaled, themed slide stack in the
  real app; Present enters fullscreen and steps slides; a workspace image on a
  slide loads through the file API; a plain `.md` is visually unchanged.
- Web-demo GIF attached (real app, visible cursor).

## Follow-ups (not this PR)

- KB document viewer (`KbDocBody`) rendering Marp too.
- Custom theme registration (`themeSet.add(css)` from a workspace/app CSS).
- Optional: an agent skill/prompt that authors `deck.md` (the "produce" half).
- Optional: re-enable JS fit-scaling (run marp's script safely) if fit-headers
  matter.
