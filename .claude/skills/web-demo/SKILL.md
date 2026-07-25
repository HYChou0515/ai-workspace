---
name: web-demo
description: Record a GIF or screencast of THIS project's web app (web/) with a VISIBLE mouse cursor clicking through the UI. Builds the SPA, serves the static bundle with /api proxied to the running backend, drives it with Playwright (injecting a visible cursor since headless has none), and converts to GIF with ffmpeg. Use when the user asks for a gif/video/screencast/recording/animated demo of the web app or a UI flow, "show the pointer clicking", "record me clicking the icons", or wants to visually demonstrate how a feature works.
---

# web-demo — a cursor-driven GIF/screencast of the web app

Produce a GIF (and/or webm) of the **real** web app with a visible mouse cursor
moving and clicking through a flow. Everything runs headless; the cursor is drawn
by us.

## Why not `pnpm dev`
The Vite **dev** server compiles on demand and is memory-heavy — in a constrained
sandbox/cgroup it gets OOM-killed (SIGKILL / exit 137) even with plenty of host
RAM. So build ONCE and serve the static bundle, which is light.

## Prerequisites
- The **backend is running** and reachable — the app needs its `/api`. Confirm
  (adjust the port to wherever the backend serves, usually 8000):
  `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/apps` → `200`.
- `ffmpeg` (`which ffmpeg`) for GIF conversion.
- Playwright + Chromium — the `playwright-skill` executor, or a local `playwright`.

## Steps

1. **Build the SPA** (once): `cd web && pnpm install && pnpm build` → `web/dist`.

2. **Serve dist + proxy /api** with `scripts/serve-dist.js` (static files + a
   `/api` reverse proxy to the backend — light, won't OOM). Set `DIST`, `PORT`,
   and the backend `API` port at the top, then:
   ```bash
   setsid node .claude/skills/web-demo/scripts/serve-dist.js > /tmp/serve.log 2>&1 < /dev/null &
   sleep 2 && curl -s -o /dev/null -w '%{http_code}\n' http://localhost:<PORT>/      # 200
   ```
   Pick a **free** PORT — an in-use one silently serves someone else's build, so
   sanity-check that the data you see is YOUR build.

3. **Write the tour**: copy `scripts/record.template.js`, keep the header
   (injected cursor + `move / click / hover / dismiss` helpers + video recording),
   and fill in the `TOUR` block. Drive by **role/selector**, never hardcoded pixels.

4. **Run it** (prints the `.webm` path):
   ```bash
   cd <playwright-skill-dir> && node run.js /abs/path/to/record.js
   ```

5. **Convert to GIF** (palette for color, scaled + fps-capped for size), then send:
   ```bash
   ffmpeg -y -i tour.webm -vf "fps=11,scale=860:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=200[p];[s1][p]paletteuse=dither=bayer" tour.gif
   ```
   `SendUserFile(['tour.gif','tour.webm'], display:'render')` — the GIF previews
   inline; the webm is smaller and crisper.

6. **Clean up**: `pkill -f serve-dist.js`; delete any throwaway preview pages.

## Gotchas (learned the hard way)
- **Dismiss the first-visit welcome/onboarding modal first** — it overlays the
  gallery and eats the very first click. The template's `dismiss()` clicks
  "Got it"; call it after the initial load AND after entering an App.
- **Use `locator.click()`, not `mouse.down()/up()`** — a manual down/up does NOT
  trigger a React-Router `<Link>` navigation, so the tour silently never leaves
  the first page. Glide the cursor with `page.mouse.move(x,y,{steps})` for the
  *visuals*, then `locator.click()` for the *action* (the cursor is already there).
- **SSE keeps connections open** → `waitUntil:'networkidle'` never fires. Use
  `'domcontentloaded'` + explicit `waitForTimeout`.
- **The cursor is a DOM overlay** with `pointer-events:none` (never intercepts
  clicks), moved from a capture-phase `mousemove` listener, injected via
  `context.addInitScript` so it survives navigations.
- Keep the GIF small: fps ~10–12, width ~800–900, trim or speed up long tours.
  A ~45 s tour ≈ 3 MB GIF / 1.3 MB webm.

## Frame-check before sending
Extract a mid frame (`ffmpeg -ss <t> -i tour.webm -frames:v 1 f.png`) and LOOK at
it — confirm the cursor is visible and the tour actually reached the intended
screen (not stuck behind a modal or on the landing page). Re-record if it drifted.
