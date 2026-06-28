# Plan — `make_deck` runtime + craft (follow-up to #284)

> `make_deck` shipped (#284) but does **not actually run** on the production
> sandbox, and its craft is a compressed subset of the proven `designed-pptx`
> skill. This is the follow-up that makes it run, fail loud when it can't, and
> ports the full craft as a reusable, extensible helper library.

Grilled (`/grill-me`) and locked. Sister issue #285 (`sci-plot`) already shipped
as an image-agnostic tool package; this plan deliberately does **not** chase that
shape for `make_deck` — see decision 2 below.

## Root cause (why it doesn't run today)

* Production runs the **HTTP sandbox-host** (`sandbox.kind: http`). The host uses
  `IsolatedProcessSandbox` — it jails processes inside **its own image** and
  **ignores `SandboxSpec.image`**. So `sandbox_image` (and the
  `docker/Dockerfile.workspace-deck` it would select) is a **no-op on the prod
  path**.
* The sandbox-host image (`sandbox-host/Dockerfile`) carries only
  python + util-linux + acl. **No `node`, no `libreoffice`, no `pdftoppm`.**
* `make_deck` is listed in `rca` + `playground` tools and runs `node build.js` /
  `bash render_deck.sh` in the turn sandbox → on prod it fails every round and
  returns a vague "Could not build a deck".
* `sci-plot` / `python-stack` / `rca-tools` work over HTTP because they ride the
  `/.tools` prebuilt-bundle mechanism — **python-only, relocatable**. `node` and
  `libreoffice` cannot ride it (node ≠ uv venv; LibreOffice is a large native
  app), which is why `make_deck` is the one tool whose deps must live in the host
  image.

## Locked decisions (grill)

1. **Scope** — all three layers (runtime / craft / extensibility), prioritised
   as the phases below.
2. **Provisioning** — accept that `make_deck` needs a heavy sandbox image; **drop
   image-agnostic parity with `sci-plot`** (blocked by LibreOffice rendering).
   When the toolchain is absent, **fail loud**, never silent.
3. **Prod path = HTTP sandbox-host** → the real fix targets the **sandbox-host
   image** (`sandbox-host/Dockerfile`), not `sandbox_image`.
4. **(甲)** Bake `node + pptxgenjs + libreoffice-impress + poppler-utils +
   fonts-noto-cjk` into the **single** sandbox-host image; the whole deployment
   shares it. Lean deployments can run a host without it and degrade gracefully
   via the preflight guard.
5. **(乙 → full)** Port the craft as a **require-able helper library**
   (`recipes.js`) — the generated `build.js` `require`s it; the prompt only
   documents the API + when-to-use. This keeps the prompt small (works for local
   models) and **is the developer extensibility surface** (the #285 spirit: add a
   layout = add a helper). Port the **full** designed-pptx set (8 layouts + the
   visual-recipe components).

## Phase 1 — make it actually run (runtime)

* **`sandbox-host/Dockerfile`** host stage: add `nodejs npm libreoffice-impress
  poppler-utils fonts-noto-cjk fonts-liberation` + `npm install -g
  pptxgenjs@^3.12`. Pin versions; reuse the proven set from the (to-be-deleted)
  `docker/Dockerfile.workspace-deck`.
* **NODE_PATH / jail passthrough** (the one real risk): ensure a jailed process
  resolves `require('pptxgenjs')`. Try image `ENV NODE_PATH`; if the `setpriv`
  jail strips env, install pptxgenjs where `node` resolves by default. Pinned by
  the integration render test.
* **Preflight guard** in `run_make_deck`: before the loop, `exec_run` a
  `command -v node && command -v soffice && command -v pdftoppm`; on failure
  return an actionable `error: …` (don't burn rounds + LLM calls).
* **Cleanup**: delete `docker/Dockerfile.workspace-deck`; fix the
  `configs/config.example.yaml` comment that points at it; reconcile
  `docs/plan-issue-284.md` decision 9 (toolchain lives in the sandbox-host image
  on the HTTP model).

Tests: unit (CI) — preflight-missing returns the error + skips the loop;
`sandbox-host/Dockerfile` text asserts the pinned packages. Integration
(`@pytest.mark.integration`, full local suite) — real sandbox runs a minimal
`node build.js` → `render_deck.sh` → `slide-*.jpg`.

## Phase 2 — port the full craft as a library (craft + extensibility)

* **`assets/recipes.js`** (new, require-able): full faithful port of the proven
  designed-pptx code — layouts (`pageHeader`, `cover`, `twoCol`, `threeCol`,
  `steps`, `cardGrid`, `callout`, `decisionMatrix`, `closer`, `pageNum`,
  `motif`) + components (`codeBlock`, `graph`, `coverMotif`, `flow`,
  `calloutBanner`, `chip`, `pullQuote`, `ySplit`, `navStrip`). Composable
  building blocks, absolute coords, `theme.js` tokens — not rigid templates.
* **`CraftAssets`**: replace `starter_js` with `recipes_js`; `build_deck` writes
  `theme.js` + `recipes.js` + `render_deck.sh`.
* **`assets/system.md`** rewrite: document the `recipes` API + when-to-use each
  layout + curated pptxgenjs gotchas + a small end-to-end usage example (the
  model copies *this*, since it can't read sandbox files) + output contract
  (`require('./theme')` + `require('./recipes')`, write to out_path). Kept lean.
* **Retire `assets/starter.js`** (the model never saw it). The usage example
  lives in `system.md`; a usage header comment lives atop `recipes.js`.
* **Extensibility**: a developer adds a helper to `recipes.js` + one line to
  `system.md`. v1 is hand-documented (future: JSDoc-generated API table).

Tests: unit (CI) — `CraftAssets.load()` reads `recipes.js`; `build_deck` writes
it; prompt assembly references the recipes. Integration (full local) — render a
CJK deck that `require`s `recipes.js`.

## Phase 3 — verify + close

* **Live canned check** (DoD): run one real `make_deck` turn with the configured
  `kb.deck_vlm`; eyeball the slide JPGs (designed look + the review loop fixes a
  layout bug).
* **Gate**: full suite + `coverage … --fail-under=100`; `ruff`; `ty` (whole
  project).
* **Docs/PR**: cite the `designed-pptx` skill as the craft source in the PR body.

## Known trade-offs / risks

* **Image size** — LibreOffice + noto-cjk bloat the sandbox-host image; accepted
  (甲). Preflight lets a lean host degrade gracefully.
* **NODE_PATH passthrough** — the only technical unknown; the integration test
  pins it.
* **recipes.js is JS** — not under the Python coverage gate; it is a faithful
  port of proven code, exercised by the integration + live checks.
