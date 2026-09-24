# Plan — runtime view plugins, a chart plugin, stacked maps and a gallery (#847 + #848)

Grilled 2026-09-25 (Q1–Q21) on master `6488a7ac`. Nothing under `src/` or `web/src`
changes until each PR's own plan is agreed:

- [PR 1: the plugin platform](plan-view-plugins-pr1-platform.md)
- [PR 2: the chart plugin](plan-view-plugins-pr2-chart.md)
- [PR 3: markings and layouts](plan-view-plugins-pr3-marking-layout.md)
- [PR 4: stack and gallery](plan-view-plugins-pr4-stack-gallery.md)

Every decision is tagged with who made it:

- **[user]** means the user proposed or required it.
- **[agreed]** means I proposed it and the user said yes.
- **[unconfirmed]** means I proposed it and the user did not answer it. It is a working
  assumption, not a decision. None remain: Q19's scenarios were confirmed on
  2026-09-25.

## What this is

#847 asks for interactive charts where people can brush and link across views. #848
asks for stacked wafer maps and a thumbnail gallery. Both need a surface the app does
not have today (verified on `6488a7ac`):

- `web/package.json` has no chart library.
- The view kinds are `table`, `board` and `gantt`, which render entity records, plus
  `health` and `wui`.
- A `*.ai.yaml` file already renders as a live view. The `aiview` entry in
  `web/src/renderers/registry.ts:112` routes it to `AiYamlRenderer`, which dispatches
  on `view:` through `registerViewKind` (`web/src/renderers/entity/viewKindRegistry.tsx:54`).
  So #847's "to do (a)" already exists.
- Second-party kinds are compiled in. They live in `web/src/ext/`, which `main.tsx`
  imports, and `docs/view-kind-authoring.md` says "this is not hot-plug".

The outcome is four things:

1. **Runtime view plugins.** A second party adds a `*.ai.yaml` renderer without
   touching this repo's source or rebuilding the SPA **[user, Q14]**.
2. **A chart plugin shipped the same way**, as the reference example **[user, Q14]**.
3. **Named markings** (linked selection) across the workspace's own split panes
   **[user, Q5]**.
4. **Stack, diff and facet (gallery)** as generic chart building blocks, sized for
   hundreds to thousands of groups **[user, Q11]**.

## Decisions

### Scope

- **Q1.** Do all of #847 and all of #848 **[user]**. Do not narrow to a "wafer slice".
- **Q8.** `sci-plot` is deleted in prod. Design as if it does not exist **[user]**.
  An AI that needs a chart as evidence writes a `view: chart` spec and calls `show_file`.
  If a static image is ever needed, the AI runs matplotlib in the sandbox itself.
- **Q19.** Trigger rate (whether the AI actually summons charts) is **not** part of the
  DoD **[user]**. Whether a model uses a skill depends on the model, and prod runs a
  model we do not see, so a local number neither passes nor fails prod.
  - Ship scenarios anyway, unscored, as the operator's tool for retuning the skill on
    their own model **[user]**. This follows CLAUDE.md's `skill_eval` rationale.
  - **Retuning must be simple for the operator [user].**
    - Scenarios travel inside the plugin as `<plugin>/scenarios/`, a sibling of
      `skill/`, so they are never copied into workspaces.
    - One command, `view_plugin tune <name>`, runs `skill_eval` on the installed
      `skill/SKILL.md` with those scenarios plus `--control`.
    - The operator edits that one file in the plugin dir and reruns. No rebuild and no
      repo change are needed.

### Rendering

- **Q2.** Use ECharts, tree-shaken and bundled, with no CDN because the deployment is
  air-gapped **[agreed]**. Deciding facts:
  - ECharts has native polygon (lasso) brush. Vega-Lite has only interval and point.
  - ECharts renders on canvas and has `large`/`progressive` modes.
  - ECharts ships inside the chart plugin bundle, not in the SPA's main bundle (Q14).
- **Q7.** The spec language is a **Vega-Lite subset written as YAML**, translated to
  ECharts **[agreed]**.
  - Models know Vega-Lite well, which matters most for small local models.
  - `mark` / `encoding` / `transform` / `facet` is a proven design to copy.
  - An unknown key is a loud error, the same rule as the config loader.
  - `source`, `keys`, `marking` and `highlight` are our own keys.
  - Inline `data.values` is not accepted. Data stays in files.
- **Q20.** The mark list **[user: "都要做"]**:
  - Marks: `line`, `bar`, `scatter`, `heatmap`, `grid`, `area`, `pie`, `boxplot`,
    `rule`, `text`, `errorbar`. `rule` / `text` / `errorbar` were added because each one
    serves a claim (spec limits, labels, variance) **[agreed]**.
  - Axes: quantitative, categorical and temporal.
  - Transforms: `aggregate`, `filter` and diff.
  - Facet.
  - Interactions: tooltip, box brush, lasso and legend click.
  - Scatter above about 10k points is binned in the sandbox. The threshold is a spec knob.
  - Not done: `geoshape`, `image`, `trail`, `tick`, `errorband`. They fail validation
    loudly.
- **Q13.** One `grid` implementation with two hosts **[agreed]**:
  - The core is `(cells, colour scale) → ImageData`, with one colour scale.
  - A thumbnail paints that ImageData on a plain canvas, with no ECharts instance per
    thumbnail.
  - A full view embeds the same ImageData as an ECharts `graphic` image. ECharts supplies
    the axes, tooltip, legend and brush. Lasso hit-testing on cells is ours.
  - A parity test pins "same cells → same pixels" on both paths.

### Knowledge

- **Q6.** The mechanism is **knowledge-free** **[user]**.
  - A marking is `column name → set of values`, and both are opaque strings.
  - Which columns link is written in each spec's `keys:` by its author, usually the AI.
  - Views link on same-named columns.
  - `highlight:` is either a predicate (`where:`, evaluated with pandas `query` in the
    sandbox) or explicit values. The author chooses, and both resolve to the same marking.
  - A view without `keys:` cannot be a marking source, but it can still be lit.
- **Q6 (cont.).** Domain knowledge belongs in skills, not in the mechanism **[user]**.
  - Then: **no wafer knowledge ships at all** **[user, Q16]**. The chart plugin's skill is
    written generically ("a value's spatial pattern on a 2-D lattice → `grid` + a
    cross-group aggregate"; "many groups share a shape → facet, sorted, the matching ones
    marked").
  - Wafer data appears only as test and live-check fixtures.

### Data and compute

- **Q3.** The source is a table file in the workspace, referenced by `source:`, in long
  format with one row per cell **[agreed]**.
  - Per-group attributes such as sort keys are optional columns.
  - CSV first. Parquet is in scope too (Q11).
- **Q4.** Aggregation runs **in the item's sandbox through `exec`**, and the browser
  receives only results **[user]**.
  - This needs no new sandbox protocol op and no sandbox-host change.
  - The uid/cgroup/path isolation is the one `exec` already has.
  - Costs to state:
    - Opening a view wakes a cold sandbox, including its restore.
    - Every call pays a process start.
    - A user can `pip install --upgrade pandas` into their user-site (#581), so
      sandbox code must be tested against the version it actually runs.
- **Q11.** Hundreds to thousands of groups, so virtualization and paged fetching are
  mandatory **[user]**. Sized by rule of thumb, not measured **[user]**. Cost model
  **[agreed]**, assuming 1000 groups × 5000 cells and 20 B per CSV row, which gives
  about 100 MB:
  - An `exec` with a pandas import costs about 0.3–1 s per call.
  - `read_csv` runs at about 50–100 MB/s, so 1–2 s for this file.
  - Transfer at 1 B per cell is about 5 KB per group, or about 500 KB for 100 groups:
    tens of ms on a LAN.
  - JSON floats run about 30–40 KB per group, and parsing costs more than the transfer.
  - Painting with `putImageData` takes under 1 ms per thumbnail.

  What that means:
  - Re-reading the source dominates. It happens **once per (source version, spec)** and
    builds a cache: an index of per-group sort keys and offsets, plus fixed-size
    per-group records. Pages are byte slices of that cache.
  - **No downsampling.** Full resolution is cheap, and downsampling erases a thin edge
    ring.
  - **Binary, not JSON.** Categories are 1 byte. Continuous values are quantized to 256
    levels for colour, and the exact value is fetched on enlarge.
  - At 50k cells per group, a page must be tens of groups, and a 1 GB CSV takes 10–20 s
    to read, so **parquet ships in this plan** **[user]**.
- **Q12.** The cache lives in `.home/.cache/views/`, the per-sandbox infra area
  **[agreed]**.
  - It is not in the quota, not in the tree and not backed up, and it is reaped with the
    sandbox.
  - A page read is a light `exec` with no pandas that returns base64. By rule of thumb it
    costs about 100 ms.
  - The scratch disk is outside the quota, so the cache builder enforces an LRU cap
    (default 500 MB).
- **Q18.** Entity records as a chart source go through the **same sandbox path**
  **[agreed]**.
  - The plugin calls an SDK-side `read_entity_records(type)`, maintained by the
    platform, so the plugin never parses `N.md` itself.
  - A parity test uses `EntityStore.query` as the oracle.
  - **Future direction [user]:** simplify entities themselves into this model. That is
    not in this plan.

### The AI side

- **Q9.** Before `show_file` shows a `.ai.yaml`, the owning plugin's `validate` runs in
  the sandbox **[agreed]**.
  - It checks the spec keys, that the source and columns exist, and that `highlight`
    matches neither none nor all rows.
  - Failure returns an error and shows no card. This is today's rule that an
    unresolvable path shows nothing.
  - Success appends a one-line summary to the tool reply, e.g. "highlight matches
    3/25 groups; fail_rate 0.02–0.41".
  - The check is deterministic, with no VLM. VLM review of the rendered look is out of
    scope because it needs a second renderer.
- **Q10.** The reverse direction, human to AI **[agreed]**:
  - On send, the active markings appear as removable chips above the composer and
    persist with the message.
  - Each marking is written to `.markings/<name>.json`, which the AI reads with
    `read_file`.
  - The prompt gets one line per marking: name, value count per key and path.
  - A selection that is never sent writes nothing.

### Layout and linking

- **Q5.** Use the **workspace's own split panes** (`paneTree.ts`, `useEditorGroups`), not
  a new `view: page` **[user]**.
  - Each view is its own `.ai.yaml` in its own tab.
  - Linking scope is one item.
- **Q5.1.** **Named markings** in the Spotfire style **[agreed]**:
  - `marking: <name>` in a spec. Only views on the same marking link.
  - The default is none.
  - The view header shows `🔗 <name> ▾`, where the user can re-attach or detach the view.
- **Q5.2.** `show_file` takes a `layout`, with the `PaneNode` shape and paths as leaves
  **[user]**, and renders as one card.
- **Q5.3.** Chat mode opens an **editor-area-only workspace page**: panes, views and
  markings, with no file explorer and no chat **[user]**.
  - Today chat mode opens the raw `fileUrl`, so a `.ai.yaml` card shows YAML text.
    Fixing that is required anyway.
- **Q17.** Where a layout card opens **[user]**:
  - With one pane today: the card's layout replaces it, and all existing tabs move into
    its **top-left** pane.
  - Already split: the whole existing tree moves **left**, and the card's layout opens
    on the **right**.
  - A card file that is already open has its tab **moved** into place, not duplicated
    **[agreed]**.

### Plugins

- **Q14.** Runtime plugins in the Grafana style **[agreed]**:
  - Each plugin is a built ES module in an operator-configured directory.
  - The SPA fetches `GET /api/view-plugins` and `import()`s each one before first render.
  - An import map points `react` and the SDK at the host's single copy.
  - `registerViewKind` is unchanged. There are two delivery channels (build-time `ext/`,
    runtime dir) and one registration rule.
  - The SDK is versioned, and a mismatch is a loud per-panel error.
  - A `view_plugin new` scaffold is provided.
  - Trust model: plugins run in the SPA origin with the user's full rights. They are
    **operator-installed and operator-trusted**, never user-uploaded. User-authored
    pages stay in the WUI iframe sandbox.
- **Q15.** A plugin may carry a **sandbox half**, and the platform exposes one generic
  runner **[agreed; user: "this is what a plugin must have, including its skill"]**.
  - Correction found while grilling: `exec` has **no stdin** (`sandbox/protocol.py:296`).
    The runner is the existing `exec_package_command` (`tooling/registry.py:319`), which
    runs `/.tools/<pkg>/launch <cmd> <args_json>`. WUI's `callTool` already uses it.
  - One argv string is limited to about 128 KB, so large inputs such as highlight lists
    pass a `.markings/` path, not inline values.
- **Q16.** A plugin is **one folder with four parts**, all wired at boot **[agreed]**:
  - `plugin.json`
  - `web/index.js`
  - `sandbox/`, a standard prebuilt tool bundle mounted under `/.tools`
  - `skill/SKILL.md`, which becomes a shared skill, so `SHARED_SKILLS` gains plugin
    sources
  - optionally `scenarios/`, the unscored `skill_eval` set that `view_plugin tune` runs

  How it reaches agents:
  - Its `plugin.json` `views` entries become lines of a new `## Available views` index.
  - Apps get the plugin's skill and index lines **by capability, not by list**: any app
    whose agent has both `write_file` and `show_file` gets them, because editing
    `app.json` is a source change.
  - `skill_prefs` can turn a plugin's skill off per item.
  - Rendering is everywhere, because a file is a file.

### Delivery

- **Q21.** Four stacked PRs, each run through `/tdd` and its own review rounds, with a
  `docs/migrations.md` entry wherever the operator must act **[agreed]**. This plan goes
  straight to master **[user]**.

## Checks that must happen before building on an assumption

Each of these is the first phase of the PR that depends on it:

1. **Tool bundle loading is strict** (`__main__.py:156-205`), and `/.tools` is **one
   mounted root** (`tools_dir = tools_root`). Plugin bundles must end up under that root,
   or the mount must learn several roots. (PR 1)
2. **The carrier has no `pyarrow`**, and the repo has none either. The chart plugin's
   sandbox half is its own prebuilt bundle with its own venv, carrying pandas and
   pyarrow. Whether #581's user-site `PYTHONPATH` prepend reaches a bundle's venv decides
   whether "the user upgraded pandas" can break it. (PR 2)
3. **Import maps with Vite.** The host must emit React and the SDK as stable, separately
   addressable ES modules, and the import map must be in place before the first plugin
   `import()`. `SPA_CSP` (`api/spa.py:34`) sets only `frame-src`, so an inline import map
   is not blocked today. Re-check this if a `script-src` is ever added. (PR 1)
4. **Docker.** The stages are `web`, `app`, `chat-video`, `api`, and `api` is last, so it
   is the default target. A plugin build stage must not be appended after it. (PR 1)

## Not in this plan

- VLM review of rendered charts (Q9).
- Cross-item markings (Q5).
- #850's own acceptance, the JMP variability chart. Only the facet mechanism is shared.
- #852 canvas pinning.
- Making entities plugin-style (Q18 future direction).
