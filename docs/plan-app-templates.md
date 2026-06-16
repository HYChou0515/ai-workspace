# Plan — RCA → App templates (#89)

Make the platform **multi-app**: RCA becomes one **App** produced from an in-code
template dir; more Apps (e.g. "FOO") are added by dropping a dir. Each App is a
self-contained, separately-branded parallel dashboard. Vocabulary: see
`CONTEXT.md` → "Apps & work items". Grill record: this doc + the #89 grill.

**Clean break.** Not in production; **all data is wiped** — no migration. The old
single-`Investigation` model and `agents.workspace_chat` picker are removed by the
end (see "Old `Investigation` lifecycle" below) — but the *migration itself is
incremental*: the new model **coexists** with the old one through P1–P7 so no
phase leaves the app in a half-broken state.

## Old `Investigation` lifecycle (when the old model dies)

The new per-App `RcaInvestigation` is added **alongside** the old `Investigation`
in P1 and both stay registered (old = legacy, untouched by new code) through
P1–P7. The old model + its consumers are **deleted in P8**, once nothing
references them:

- **P1**: add new model; old `Investigation` untouched.
- **P2**: `Conversation` re-keys to `item_id` → the old `Ref("investigation")`
  link is gone, but `Investigation` itself still exists.
- **P3–P4**: new AppCatalog / API (per-App routes + generic create-with-seed)
  stand up next to the old `POST /investigation` + `rca/templates` seeding.
- **P5–P7**: FE migrates to the new `/a/:slug` routes.
- **P8 — delete**: remove `resources/investigation.py` (`Investigation` /
  `Severity` / `Status`), the old `POST /investigation` route + list/get wiring,
  the `rca/templates` seeding path, and `rca/` remnants (now under `apps/rca/`).
  Drop its specstar registration. After P8 no `Investigation` references remain.

## Locked decisions (recap)

1. **Per-App resource (β)** — each App hand-writes a `msgspec.Struct` subclass of
   `WorkItemBase` in `apps/<slug>/model.py`; data is **not** mixed across Apps.
2. **`WorkItemBase`** — Tier 1 (`title`/`owner`, concrete) + Tier 2
   (`members`/`topics`, `T | UnsetType`, opt-in). Tier 3 = App's typed domain
   fields (RCA: `severity`/`status`/`product`).
3. **item_id** — shared machinery (FileStore / Conversation / sandbox) keys on the
   WorkItem's `resource_id` (globally unique; never `uid`). `Conversation.item_id:
   str` opaque+indexed; deletion via per-App **on-delete event_handler** (no
   declarative Ref/cascade). FileStore already keys on `workspace_id` → unchanged.
4. **App dir** = `apps/<slug>/`: `app.json` + `model.py` + `prompts/` + `profiles/`.
5. **Agent (3-layer)** — App owns the ceiling (`picker` / `tools` / base
   `prompt_file`); **profile** picks subsets (`tools ⊆`, `presets ⊆`,
   `default_preset`) + `_prompt.md` appendix + `.skill/` + `suggestions`;
   **preset** (config.yaml) gives model + creds + sandbox_image + idle_timeout.
   Resolve in a new **`AppCatalog`**.
6. **config.yaml** — remove `agents.workspace_chat`; keep `agents.presets` + the
   KB usage entries; `AgentConfigCatalog` shrinks to `kb_chat` / `infer_modules`.
7. **Routing** — `/` launcher (always shown: App cards + a KB link card),
   `/a/:slug` App home, `/a/:slug/:itemId` workspace. URL-first (see #93 note).
8. **identity / theme** — `app.json`: `slug` / `title` / `description` /
   `icon` (svg-file | emoji | named) / `color` (one hex → `--accent` trio, full
   re-theme inside the App). `item.{noun, noun_plural, create_label}` drives the
   FE's human-readable item strings.
9. **function toggles** — `workspace` (file IDE + file tools + seeding),
   `sandbox` (exec + package tools), `terminal` (human shell; needs sandbox).
   `tools[]` ↔ toggle incoherence = **startup hard error**.
10. **fields (minimal)** — kinds: `text` / `select` only (number/date later).
    Types/options from the model's OpenAPI schema (enum → select); `app.json`
    `layout` (`breadcrumb`/`statusbar`/`list`/`form` field lists) + optional
    `labels` is a display overlay. Fields **inline-editable** on those surfaces.
    No per-field filter/sort engine; home = free-text search + universal tabs.
11. **index** — native per-resource `INDEXED_FIELDS`. (specstar Discussion #368 on
    dict-path indexing is parked — no longer on the path now that fields are typed.)

## #93 alignment (TanStack Router, later)

[[#93]] will migrate FE routing to TanStack React Router so every common page is
URL-addressable. Design #89 routing **URL-first** now so that migration is
mechanical: each common view gets its own route, **including the create flow**
(profile-pick + form is a route like `/a/:slug/new`, not modal-only state). Don't
deepen `react-router-dom`-specific coupling. Stack today is `react-router-dom`.

## Phases

> Per CLAUDE.md, drive each phase with `/tdd`. Targeted tests per change; full
> suite + 100% coverage gate once at the end.

- **P1 — Base model + per-App resource (backend).** `apps/` package + `apps/base.py`
  (`WorkItemBase`). Port RCA → `apps/rca/model.py` (`RcaInvestigation` + `Severity`/
  `Status` enums + `product`) with `MODEL` + `INDEXED_FIELDS`. Register it (small
  helper; P3 makes it a dynamic `apps/` scan). **Coexists with the old
  `Investigation` — no deletion here** (old model dies in P8; see lifecycle above).
  Data is wiped at cutover.
- **P2 — Shared machinery re-key (Conversation decoupling).** *Done.*
  `Conversation.investigation_id` (typed `Ref` + cascade) → `item_id: str`
  (opaque, indexed) so one Conversation table serves every App's items;
  updated the index + the create/query call sites in `api/app.py` + the tests
  that read the field. No test relied on the old cascade, and nothing deletes
  item resources today (close = status flip, not delete), so dropping the Ref is
  not a regression. **Deferred (non-load-bearing here):** (a) the **on-delete
  cleanup handler** (specstar `OnSuccessDelete` confirmed) that removes an item's
  conversation + files + sandbox — wired in **P3/P4** at the app layer where
  `FileStore` + `InvestigationRegistry` are available (`rm.event_handlers.extend`
  post-`add_model`, per house style); (b) the **cosmetic** rename of the still-
  named `investigation_id` field/var on `AgentToolContext` / `InvestigationSession`
  / `KernelHandle` — those values are already treated as opaque ids, so the rename
  is clarity-only and folded into a later refactor (P8).
- **P3 — App manifest + profiles + AppCatalog (additive).** *3a–3c done.*
  `app.json` schema + `load_app_manifest` (3a); profile loader + ported
  default/tool-demo (3b); `AppCatalog` 3-layer resolve (app ◇ profile ◇ preset) +
  subset validation + `validate_function_coherence` + ported base prompt (3c).
  **3d (re-scoped, additive):** wire an `AppCatalog` into `factories` /
  `create_app` (from `agents.presets`) + run coherence validation over discovered
  apps at startup — **no live behaviour change**. The picker/resolve **cutover**
  moved to **P4**; removing `agents.workspace_chat` + shrinking `AgentConfigCatalog`
  moved to **P8** (both are old-path cleanup, sequenced with the old-Investigation
  removal — a standalone cutover now would need a throwaway
  old-Investigation→app/profile/preset bridge and would prematurely break the 9
  config test files).
- **P4 — API + agent cutover (on the new model).** specstar per-model CRUD exposed
  (manifest carries each App's resource-type slug — don't wrap to hide, per house
  style). Generic `POST /a/:slug/items` create-with-seed (create the
  RcaInvestigation + seed the chosen profile). `GET /apps` (launcher summaries) +
  per-App manifest endpoint (inlined icon SVG, layout, nouns, function flags,
  picker). **Cutover (from P3d):** `/agent-configs` picker + the per-turn resolve
  switch to `AppCatalog.resolve(app_slug, profile, attached_preset)` on the new
  RcaInvestigation (no bridge — the new record carries app/profile/preset directly).
- **P5 — FE launcher + routing (URL-first).** `/` launcher per
  `design_handoff_rca_3.0` — implement **direction B** (`LauncherScreenB` in
  `rca/launcher.jsx`; spec in README §0 + `App Launcher.html`): neutral platform
  header (swap the "Workspace" placeholder wordmark for the real platform brand
  later) + responsive feature-card gallery; App cards (top accent bar / 54px icon
  tile / footer route+arrow / `color-mix` hover from the single hex / focus ring)
  + the fixed dashed KB link card; cover all four states (normal / one / empty /
  loading). Tokens are sourced from `rca/system.jsx` (e.g. `--accent` `#F0502E`,
  `--paper` `#F1ECE0`) — not my earlier guessed values. Routes `/a/:slug`,
  `/a/:slug/:itemId`, `/a/:slug/new`. Per-App `color` → `--accent` trio re-theme
  on entering an App (launcher itself stays neutral, per-card-local color only).
  (README §2's `/investigation/:id` is stale — use the Routing section's
  `/a/:slug/:itemId`.)
- **P6 — FE App home (generic dashboard).** Item list scoped to the App's
  resource; columns from `layout.list` (types from schema); free-text search +
  universal tabs (all/mine/pinned/recent). Start button (`item.create_label`) →
  `/a/:slug/new` (profile-pick if >1, skip if 1) → generic create form
  (`layout.form` + title + Tier 2 if enabled) → create+seed → workspace.
- **P7 — FE workspace shell (generic).** Generalize `InvestigationShell`: panes
  gated by `function` flags; `breadcrumb`/`statusbar` render `layout` fields,
  inline-editable (schema-driven: enum → select, str → text); agent panel uses the
  App's picker + profile suggestions.
- **P8 — Delete old `Investigation` + old agent path + cleanup + live check.** Per
  "Old `Investigation` lifecycle": delete `resources/investigation.py` + its
  specstar registration + the old `POST /investigation` / list / get wiring + the
  `rca/templates` seeding path + `rca/` remnants. **Remove `agents.workspace_chat`**
  from config schema / loader / merge / catalog_build and **shrink
  `AgentConfigCatalog`** to the KB purposes (`kb_chat` / `infer_modules`) — the
  old-path cleanup deferred from P3d. Verify the RCA App reproduces current
  behavior; remove remaining dead FE code (global `TemplatesModal`, RCA-specific
  bits in `HomeMain`). Live canned check (per house rule: LLM features need a live
  check). **When porting the remaining RCA profiles, drop the canvas + 5-Why seed
  files** (legacy cruft — not carried forward).

## Notes

- Launcher **visual** design = `design_handoff_rca` (updated by the user); P5
  implements against it.
- specstar #368 (dict-path indexing) — parked; revisit only if a future App needs
  dynamic (non-typed) indexable fields.

## P8 closeout — outstanding TODO (no deferrals)

P8 slices 1–8 landed (generic close, mention generalize, reader generalize,
delete legacy `Investigation` world, remove `workspace_chat` + shrink catalog,
FE close-repoint + dead-code sweep, capstone `WorkspaceShell`/`item: AppItem`,
live check). Commits local (unpushed): `9562d6f e409863 1fd56d9 37c5a37 dbace78
bb381da 5e16f71 dcd3dc7 3d19fe5` + the live-check commit. FE typecheck + vitest
(489) green; live check (AppCatalog resolve → qwen3:14b) passed; backend
coverage gate re-running.

PM directive: **no deferred items** — a deferred plan item is a botched job. The
audit below is the complete close-out list; execute all, TDD + green commit each.

### T1 — Port the remaining RCA profiles (behaviour parity)
Old `rca/templates/` shipped 5 profiles; the new App (`apps/rca/profiles/`) only
has `default` + `tool-demo`, so creating an RCA item lost `local-lab` +
`smt-reflow-example`. Per "drop canvas + 5-Why" + [[feedback_no_canvas_5why]]:
- **drop** `methodology` entirely (it is the 5-Why / fishbone.canvas profile).
- **port** `local-lab` (SOP + `wafer-defects.example.csv` + `_config.json` → new
  `_profile.json` schema; clean, no canvas/5-Why).
- **port** `smt-reflow-example` **minus** `5-why.md.tpl` + `fishbone.canvas`
  (keep `brief.md.tpl`, `drift.ipynb`, `pareto.ipynb`, `data/`, `report.v1.md.tpl`).
- Translate old `_config.json` → new `_profile.json` (3-layer: `tools ⊆`,
  `presets ⊆`, `default_preset`, `_prompt.md` appendix). Verify the create flow
  (`/a/rca/new`) offers them; test the new profiles seed + resolve.

**T1b — Create-flow profile picker (P6 gap surfaced by T1).** P6 specified
"profile-pick if >1, skip if 1" but `AppNewItem` only ever creates with
`default_profile` and the manifest exposes no profile LIST — so the ported
profiles (and even `tool-demo`) are unreachable from the UI. Close it:
- Backend: fold the App's profiles (`{name, title, description}`, projected from
  `apps.profiles`) into `GET /apps/{slug}` (alongside `default_profile`).
- FE: `AppNewItem` renders a profile selector when >1, defaults to
  `default_profile`, and passes the chosen `profile` in the create body.

**T1c — old `rca/templates` dead helpers.** After T1 + slice-5's `catalog.resolve`
deletion, `compose_system_prompt` / `load_template_config` / `load_template_appendix`
have no production callers (dead). Safe to delete the 3 functions + test_templates
(keep the `rca/templates` package + dirs — needed by T1d below).

**T1d — skills subsystem still on the OLD location (surfaced while combing T1c).**
`rca/skills.py` (the agent's `read_skill` tool) reads `.skill/` dirs from
`rca/templates/<profile>/.skill/` — NOT the new `apps/rca/profiles/`. It works
today ONLY because the old dirs were retained. Two gaps:
  1. **Location split** — `list_skills(profile)` / `load_skill(profile, name)`
     take only `profile` (implicit RCA) + read `rca/templates`. Should be App-aware
     (`(app_slug, profile)` → `apps/<slug>/profiles/<profile>/.skill/`), and the
     `.skill/` dirs ported into the App profiles (local-lab has one; methodology's
     is dropped with the profile).
  2. **Skill index dropped from the prompt** — the old `compose_system_prompt`
     built a "## Available skills" index into the system prompt; the new
     `AppCatalog._compose_prompt` doesn't. The `read_skill` TOOL is still exposed
     (so the agent can discover skills by calling it), but the proactive index is
     gone — a soft regression. Re-add the index to `AppCatalog._compose_prompt`.
  Threading: `read_skill` gets `profile` from `ctx.template_profile` + needs the
  App slug too (thread it, or resolve slug from the item).

### T2 — Delete the dead `GET /templates` route
FE `useTemplates` was removed in slice 6, so `api/app.py`'s `GET /templates` +
the `from ..rca.templates import list_profiles` are dead. Remove the route + the
import; drop `rca/templates.list_profiles` if it falls fully unused (keep
`compose_system_prompt` / `load_template_config` — still used by the catalog).

### T3 — `function.workspace` / `function.sandbox` structural pane gating
Plan P7 specified "panes gated by `function` flags" but only `function.terminal`
was wired (commit `3f76be7`). Wire the rest, mirroring the terminal pattern in
`WorkspaceShell.tsx`:
- `function.workspace=false` → hide the file IDE / tree / file-op tools.
- `function.sandbox=false` → hide exec / package-install affordances.
Test with a synthetic manifest whose toggles are off (no 2nd real App needed).
Backend `validate_function_coherence` already hard-errors incoherent toggles.

### T4 — Fully remove the legacy `Investigation` TS type
After the capstone it only lingers in `web/src/api/mock.ts` (`investigations:
Investigation[]` seed + `mock.closeInvestigation`). Retype/remove that seed (the
new close path is per-App), then delete `Investigation` from `types.ts` + the
`index.ts` re-export.

### T5 — Restore a per-App agent startup sanity check
Slice 5 removed the `agent-workspace` `ToolCallCheck` (it tested `workspace_chat`)
and added no replacement, so the startup sanity matrix no longer verifies the
RCA App's agent can call tools (KB / infer_modules checks remain). Add a check
that resolves the RCA App's default config via `AppCatalog` and runs a
`ToolCallCheck` against it.

### Out of #89 scope (track separately)
- **autocrud admin-UI scaffold** — untracked `web/src/autocrud`, `web/src/routes`,
  `web/.env`, `web/.autocrudrc.json`, `web/src/index.css`, + Mantine deps in
  `web/package.json` / `pnpm-lock.yaml`. Belongs to [[#93]] (admin UI), kept as a
  schema/query reference. Decide: revert `package.json`/lock + remove, or carry
  to #93. NOT a #89 deliverable.

### DoD for closeout
All five (T1–T5) done + committed; backend `coverage run -m pytest && coverage
report` green (changed files 100%); FE typecheck + vitest green; live check still
passes. Then #89 has zero outstanding items.
