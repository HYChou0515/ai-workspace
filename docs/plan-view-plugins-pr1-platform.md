# Plan — PR 1: the runtime view-plugin platform

Part of [plan-view-plugins.md](plan-view-plugins.md), which holds the decisions
(Q14–Q16, Q9) and who made each one. This PR builds the platform and proves it by moving
the existing second-party kind `csv-table` out of `web/src/ext/` into a runtime plugin.
Base: master `6488a7ac`.

## Done means

- A plugin directory **no source file in this repo names** is loaded at boot:
  - its view kind renders a `*.ai.yaml`;
  - its sandbox command runs through the generic runner;
  - its skill appears in an eligible app's prompt;
  - its `views` entry appears in `## Available views`.
- `csv-table` works exactly as before, now delivered as a runtime plugin.
- A malformed plugin fails **loudly and by name**.

## Shape

```
<view_plugins.dir>/<name>/
  plugin.json     # {name, sdk: "1", kinds: [...], views: [{kind, when}], skill?: "skill",
                  #  sandbox?: {bundle: "sandbox"} | {artifact: "<#674 artifact URL>"}}
  web/index.js    # ES module; calls registerViewKind from "@aiws/view-sdk"
  sandbox/        # optional: a standard prebuilt tool bundle (has `launch`)
  skill/SKILL.md  # optional
  scenarios/      # optional: skill_eval scenarios for `view_plugin tune`; never copied into workspaces
```

## Phases

Each phase follows `/tdd`. Each phase is a commit, and every behaviour starts from a test
that reddens on the unfixed code.

**P1 — the dev-server import map (no product code).**

The build path is proven (master plan, check 3). What remains is whether `pnpm run dev`
can load a runtime plugin: the import map has to point at modules the dev server serves
for `react` and the SDK, and those must be the same instances the app itself uses.

- If yes, record how.
- If no, the dev loop is: build the SPA, then serve it with `vite preview` behind the
  backend proxy. Record that choice in the authoring docs.

**P2 — config and discovery.**

- Add a new `ViewPluginsSettings` section `view_plugins: {dir: …}`. The default is
  `<repo>/.view-plugins`, mirroring `.workspace-tools`, with the env override
  `WORKSPACE_VIEW_PLUGINS_DIR`.
- Add a `plugin.json` msgspec schema. Unknown keys are rejected.
- `discover_view_plugins(dir)` is strict, like tools discovery: a bad manifest, a
  duplicate plugin name, a duplicate kind across plugins, or a kind that shadows a
  built-in or reserved kind **refuses boot, with a message naming the plugin and the
  field**. A missing dir means no plugins, not an error.

**P3 — serving.**

- `GET /api/view-plugins` returns `[{name, sdk, kinds, entry_url}]` for signed-in users.
- The plugin's `web/` files are served read-only under `/api/view-plugins/{name}/…`.
  Traversal is refused, and a test pins it.

**P4 — the SPA loader.**

- Before the first render (the same slot as `import "./ext"` in `main.tsx`), fetch the
  list and `import()` each `entry_url`.
- A plugin whose `import()` throws, or whose `sdk` major differs from the host's, does
  not block the app. Every `*.ai.yaml` naming its kinds renders a **loud per-panel
  error** that names the plugin and the reason, reusing the view-panel error boundary.
- A kind nobody registered still says "Unsupported view kind".
- **A plugin that bundles its own React must not take the app down.** In the spike it
  threw during render and killed the whole host tree. So the plugin component is
  mounted inside the panel's error boundary. A test uses a fixture plugin that bundles
  React and asserts that only its panel shows the error.

**P5 — the SDK.**

- `@aiws/view-sdk` is today's `renderers/entity/public` barrel plus `registerViewKind`,
  `SDK_VERSION`, and a `useSandboxRun(plugin, cmd, args)` hook (P6).
- The import-map entry points at the host's own copy.
- `ext/imports.test.ts`'s rule ("import only the public barrel") is extended to
  `view-plugins/*/web/src`.

**P6 — the sandbox half.** Delivery follows the master plan's check 1 (user: reuse #674).

- **An isolated launch template for plugin bundles** (check 2): `python -s`, with
  `PYTHONPATH` set to the bundle's own site-packages only and no user `PYTHONPATH`
  pass-through.
  - It lives beside `_LAUNCH` in `tooling/prebuild.py`, and both are folded into
    `_builder_fingerprint`.
  - A test pins that a user-site `pandas` is **not** imported by a plugin bundle, and
    that an ordinary tool bundle still does import it, as #581 intended.
- **`kind: local`:** boot copies each `{bundle: …}` plugin bundle into a merged tools
  root beside the prebuilt ones. Copies are used, not symlinks, because the jail
  bind-mounts one root.
  - Fix `__main__.py:163`: discovery must run when `PACKAGES` is empty but plugins
    exist.
  - A name clash between a plugin bundle and a tool package refuses boot, and names
    both.
- **`kind: http`:**
  - An `{artifact: url}` sandbox half goes through #674's `POST /tools/resolve`, with
    the same resolve, cache and per-sandbox view as `external_tools`. It keeps #674's
    rule that the schema and the bundle come from the same resolve.
  - A `{bundle: …}` plugin under http means "already in sandbox-host `builtin/`". Its
    absence is a loud per-call error naming the plugin, not a silent no-op.
- **`kind: docker`:** plugin sandbox halves are unsupported, as tools are. Calls fail
  loudly.
- **The runner:** `POST /a/{slug}/items/{id}/view-plugins/{plugin}/{cmd}` takes
  `{args}`. It is authorized as `read_content` and runs `exec_package_command`
  (`tooling/registry.py:319`, the same entry WUI's `callTool` uses), returning
  `{stdout, stderr, exit_code}`.
- **Plugin commands are not agent tools.** They never enter any app's tool ceiling, and
  a test pins it.
- Arguments go in argv, so an args string over the argv limit is refused with a message
  saying to pass a file path.
- **What the operator carries over:** this phase touches `sandbox-host/` only if the
  resolve path needs it. The PR body lists every top-level directory the PR touches.

**P7 — plugin skills.**

- The shared-skill source becomes `SHARED_SKILLS` (`apps/shared_skills.py`) plus each
  plugin's `skill/`.
- Eligibility is by capability: an item turn whose **resolved** tool set holds both
  `write_file` and `show_file` sees plugin skills. It is decided on the resolved set,
  not the manifest flag, which is the lesson from #581's preamble.
- Per-item `skill_prefs` still turns one off.
- `materialize_skill` copies plugin skills like any shared skill. Verified: a
  `SKILL.md`-only skill is never copied and always reads the source live, while a
  multi-file one is frozen at first read until Refresh. The authoring docs state both.
- `skill_eval` resolves plugin skills too. Today `--dump-skill` / `--skill` look names up
  in `SHARED_SKILLS` only (`skill_eval/__main__.py:239`), so a plugin skill would be
  "unknown skill".
- **Check:** an operator's edit to `<dir>/<name>/skill/SKILL.md` must reach the next
  turn. `materialize_skill` does nothing when `/.skill/<name>/` already exists in a
  workspace, so find out whether an already-materialized copy shadows the edit. If it
  does, the tune loop is broken, and the fix belongs in this phase.

**P8 — `## Available views`.**

- Built beside `## Available skills` in `_compose_prompt` (`apps/catalog.py:241`), with
  the same eligibility as P7.
- One line per `views` entry: `` - `<kind>`: <when> ``, stating capabilities only
  (positive phrasing).
- It is absent when no plugin is eligible.

**P9 — `show_file` validates views.**

- In `show_file_impl` (`agent/tools.py:401`), when the path is a `*.ai.yaml` whose
  `view:` belongs to a plugin with a `validate` command, run it first.
  - A non-zero exit returns the error and **declares nothing** (today's rule for an
    unresolvable path).
  - A zero exit appends its one-line stdout summary to the reply.
- With no `validate`, the only check is that the YAML parses.
- Built-in kinds are unchanged.

**P10 — `csv-table` becomes the first runtime plugin.**

- Move `web/src/ext/CsvTableView.tsx` and its test into `view-plugins/csv-table/`, which
  has its own `package.json` and a Vite library build with `react` and `@aiws/view-sdk`
  external.
- `web/src/ext/index.ts` stays as the build-time channel, now registering nothing.
  `ext/` itself stays because EE uses it.
- Add a Docker stage that builds `view-plugins/*` and copies them into the image's
  default dir, placed before `api`.
- Add a Make target for local dev.

**P11 — the scaffold.**

- `uv run python -m workspace_app.view_plugin new <name> [--with-sandbox] [--with-skill]`
  writes a buildable plugin, mirroring `workflow new`.
- `view_plugin check [name]` validates `plugin.json` and the built `web/index.js` exist
  without booting the app.
- `view_plugin tune <name> [--preset P] [--app A --profile B]` is the operator's
  **one-command retune** (Q19, user):
  - it runs `skill_eval` on the **installed** `<dir>/<name>/skill/SKILL.md` with
    `<dir>/<name>/scenarios/` and `--control`, then prints the report;
  - the loop is: edit that file, rerun, done;
  - it fails loudly, by name, when the plugin has no skill or no scenarios.
- `view_plugin check` refuses a built `web/index.js` that carries its own React (the
  spike's failure mode, master plan check 3), with a message pointing at the externals
  config.
- The scaffold writes a `scenarios/` stub with one should-call and one should-not-call
  scenario.

**P12 — docs.**

- Rewrite `docs/view-kind-authoring.md`: runtime plugins are the primary path, and `ext/`
  is the build-time channel.
- Add the knob to `docs/configuration.md`.
- Add a `docs/migrations.md` entry. It is needed because `csv-table` leaves the SPA
  bundle, so an image built without the plugin stage loses it, and because of the new
  default dir:
  - 設定: none.
  - k8s·CI: the image must be built through the plugin stage, and an operator mounting
    their own dir must add `csv-table` to it.
  - 確認做完: `GET /api/view-plugins` lists `csv-table`.

## Verification

- Targeted pytest and vitest for each phase, plus `ruff check`, `ruff format --check`,
  `ty check` and `pnpm run typecheck`.
- Live check in a fresh worktree:
  - boot with a plugin dir holding `csv-table` plus a scratch plugin **not in the repo**
    that has a sandbox command and a skill;
  - open a `*.ai.yaml` of each;
  - hit the runner route;
  - read the composed prompt for the index line;
  - run `view_plugin tune` on the scratch plugin, edit its `SKILL.md`, rerun, and confirm
    the report and a live turn both see the edit;
  - repeat with a malformed `plugin.json` and read the boot error.
- Base differential: the same steps on master show `csv-table` rendering from the
  bundle.
