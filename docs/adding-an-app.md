# Adding an App

The platform is **multi-App** (#89). An **App** is an in-code directory under
`src/workspace_app/apps/<slug>/`. Dropping a new dir there produces a parallel,
separately-branded dashboard — launcher card, item list, create flow, workspace
shell, agent — all driven by the App's `app.json` + model. RCA (`apps/rca/`) is
just one App; the scaffold `apps/_template/` is a copy-me starting point.

Registration is a **scan** of `apps/` at boot (`apps/registry.py`): any dir with
an `app.json` + a `model.py` is discovered, registered, and shown on the
launcher. **No central list to edit.** (`_`-prefixed dirs like `_template` are
skipped — internal, not user-facing.)

## Quick start

1. Copy the scaffold:
   ```
   cp -r src/workspace_app/apps/_template src/workspace_app/apps/<your-slug>
   ```
   The dir name **is** the slug, so it must be a valid Python package name
   (lowercase, no hyphens): `tickets`, `audits`, `incidents`.
2. Edit `<your-slug>/app.json` — set `slug` to match the dir, then fill in the
   identity / agent / item / layout / lifecycle (reference below).
3. Edit `<your-slug>/model.py` — rename `TemplateItem` + its enums to your
   domain; set `INDEXED_FIELDS` to the fields you filter / sort / colour on.
4. Edit `<your-slug>/prompts/system.md` — the agent's base prompt.
5. Edit `<your-slug>/profiles/default/` — the starter-content the create flow
   seeds (add more profiles for a pickable variety).
6. Boot (`uv run python -m workspace_app`). The App appears on the launcher; no
   other file needs touching.

## The files

```
apps/<slug>/
├── app.json                     # identity, agent ceiling, layout, lifecycle, toggles
├── model.py                     # the WorkItem Struct (MODEL + INDEXED_FIELDS)
├── prompts/
│   └── system.md                # the agent's base system prompt
└── profiles/
    └── default/                 # a starter-content bundle (the create flow's default)
        ├── _prompt.md           # appended to the system prompt for this profile
        ├── _profile.json        # (optional) narrows tools/presets, suggestions
        ├── .skill/<name>/SKILL.md  # (optional) read_skill-loadable skills
        └── *.tpl                # files seeded into the item ($title/$owner/… substituted)
```

## `app.json` reference

| field | meaning |
|---|---|
| `slug` | the App id — **must equal the dir name** |
| `title` / `description` | launcher card text |
| `icon` | `flame` (named), an emoji, or `icon.svg` (a sibling file, inlined) |
| `color` | one hex → the App's `--accent` trio (full re-theme inside the App) |
| `function.workspace` | file IDE (tree + editor + file tools). `false` → chat-only shell |
| `function.sandbox` | exec + package tools. Needs no terminal; gates exec affordances |
| `function.terminal` | human shell tab. **Requires `sandbox: true`** |
| `agent.prompt_file` | path (relative to the App dir) of the base system prompt |
| `agent.tools` | the App's tool **ceiling**; a profile may narrow to a subset |
| `agent.picker` | `[{preset, name}]` — the model picker; `preset` ∈ `config.yaml` `agents.presets` |
| `agent.suggestions` | App-level quick-prompt chips (a profile may override) |
| `item.{noun,noun_plural,create_label}` | the human strings ("Start Investigation") |
| `layout.{breadcrumb,statusbar,list,form}` | which domain fields show on each surface |
| `layout.default_tabs` | files the workspace opens on entry (filtered to those seeded) |
| `lifecycle` | `{status_field, closing_states}` — drives the Close affordance |
| `labels` | per-field display labels |
| `field_styles` | enum option → tone token (`err`/`warn`/`ok`/`info`/`muted`) — chip colours as data |
| `default_profile` | the profile the create flow seeds when the user doesn't pick |

**Toggle coherence is enforced at boot** (`validate_function_coherence`): e.g. an
`exec` in `agent.tools` with `sandbox: false`, or `terminal: true` with
`sandbox: false`, fails the boot loud. The `_template` App ships
`sandbox: false` + file-only tools to show a workspace-only (no-sandbox) App.

## `model.py` contract

Export `MODEL` (a `WorkItemBase` subclass) + `INDEXED_FIELDS`:

- **Tier 1** (free from `WorkItemBase`): `title`, `owner`, `description`,
  `profile`, `attached_preset`.
- **Tier 2** (opt-in): `members`, `topics` — redeclare as concrete `list[str]`
  if your App uses them.
- **Tier 3**: your typed domain fields (enums / scalars). Type them so they
  index natively — list `INDEXED_FIELDS` for the ones you filter / sort / colour.

The field **kinds + enum options** are projected from the model into the manifest
(`GET /apps/{slug}.fields`), so the FE renders + inline-edits them without
restating types — `enum → select`, `str → text`.

## Profiles

A profile is a starter-content bundle. `default` is required; ship more to give
the create flow a **profile picker** (it appears when there's >1). Each profile:

- `*.tpl` files → seeded into the item on create, with `$title` / `$owner` /
  your Tier-3 fields substituted (the `.tpl` suffix is stripped).
- `_prompt.md` → appended to the system prompt for this profile.
- `_profile.json` (optional, `apps.profiles.ProfileManifest`): `title`,
  `description`, `suggestions`, `tools` (⊆ `agent.tools`), `presets`
  (⊆ `agent.picker`), `default_preset`. Omit to inherit the App's full ceiling.
- `.skill/<name>/SKILL.md` (optional): `read_skill`-loadable skills with
  `name` + `description` frontmatter; the agent gets a "## Available skills"
  index + the `read_skill` tool when the profile ships any.

## Presets

`agent.picker` references **presets** by name; presets live in `config.yaml`
under `agents.presets` (model + creds + sandbox image + idle timeout). Reuse the
bundled `qwen3-local` / `claude-opus` / `openai-mini`, or add your own.

## Constraints

- The dir name is the slug — a valid Python package name (the registry imports
  `apps.<slug>.model`). No hyphens.
- `_`-prefixed dirs are not discovered (use for scaffolds / internal helpers).
- Data is **not** shared across Apps — each App has its own resource table.
