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
  plugin.json     # {name, sdk: "1", kinds: [...], views: [{kind, when}], sandbox?: "<pkg>", skill?: "skill"}
  web/index.js    # ES module; calls registerViewKind from "@aiws/view-sdk"
  sandbox/        # optional: a standard prebuilt tool bundle (has `launch`)
  skill/SKILL.md  # optional
```

## Phases

Each phase follows `/tdd`. Each phase is a commit, and every behaviour starts from a test
that reddens on the unfixed code.

**P1 — verify the three assumptions (no product code).** Each finding is written into
this file before P2 starts.

- **Import maps.** Can the Vite build emit `react`, `react-dom`, `react/jsx-runtime` and
  the SDK as stable-named ES modules, with an inline `<script type="importmap">` in
  `index.html` ahead of the entry? Prove it in a throwaway branch: a hand-built plugin
  that `import`s `react` and `@aiws/view-sdk` renders, and there is **one** React. The
  test is that a hook in the plugin works; two Reacts throw on the first hook.
- **The tools root.** `/.tools` is one mounted root (`__main__.py:196`,
  `tools_dir = tools_root`). Read `discover_packages` (`tooling/registry.py:169`) and the
  mount code (`sandbox/local_process.py:63-67`, `:446-451`, plus the `sandbox-host`
  copy). Pick one: (a) boot links or copies each plugin's `sandbox/` into the tools root,
  or (b) the mount learns several roots. Prefer (a) if it touches neither sandbox-host
  nor the jail bootstrap.
- **Docker.** Confirm the stage order in `docker/Dockerfile` (`web`, `app`, `chat-video`,
  `api`, with `api` last and therefore the default target). The plugin build stage goes
  **before** `api`.

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

**P5 — the SDK.**

- `@aiws/view-sdk` is today's `renderers/entity/public` barrel plus `registerViewKind`,
  `SDK_VERSION`, and a `useSandboxRun(plugin, cmd, args)` hook (P6).
- The import-map entry points at the host's own copy.
- `ext/imports.test.ts`'s rule ("import only the public barrel") is extended to
  `view-plugins/*/web/src`.

**P6 — the sandbox half.**

- Apply P1's choice so a plugin's `sandbox/` bundle is discovered as a package.
- `POST /a/{slug}/items/{id}/view-plugins/{plugin}/{cmd}` takes `{args}`. It is
  authorized as `read_content` and runs `exec_package_command`
  (`tooling/registry.py:319`, the same entry WUI's `callTool` uses), returning
  `{stdout, stderr, exit_code}`.
- **Plugin commands are not agent tools.** They never enter any app's tool ceiling, and
  a test pins it.
- Arguments go in argv, so an args string over the argv limit is refused with a message
  saying to pass a file path.

**P7 — plugin skills.**

- The shared-skill source becomes `SHARED_SKILLS` (`apps/shared_skills.py`) plus each
  plugin's `skill/`.
- Eligibility is by capability: an item turn whose **resolved** tool set holds both
  `write_file` and `show_file` sees plugin skills. It is decided on the resolved set,
  not the manifest flag, which is the lesson from #581's preamble.
- Per-item `skill_prefs` still turns one off.
- `materialize_skill` copies plugin skills like any shared skill.

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
  - repeat with a malformed `plugin.json` and read the boot error.
- Base differential: the same steps on master show `csv-table` rendering from the
  bundle.
