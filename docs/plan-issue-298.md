# Plan — #298 user+AI co-create a loadable skill

> Status: **grill locked (Q1–Q10), implementing via /tdd**. Branch `worktree-issue-298-author-skill`.
> Source: issue #298「我想讓 user 可以和 ai 討論出一個可以用的 skill」+ grill (Q1–Q10).

## Why

Users have tacit needs — analysis flows, domain terminology, preferred output style — that
diverge from what the shipped agents do. #298 wants a **defined, repeatable flow** for a user
to co-create a reusable **skill** *with* the AI, then (a) carry it to another workspace by
upload, and (b) once stable, request it be baked into the initial profile. The skill must
align with the industry standard (Anthropic Skills / Claude Code: a `SKILL.md` folder with
optional `references/` + `scripts/`, surfaced by *progressive disclosure* — index in the
prompt, body loaded on demand).

We already ship that mechanism for **dev-authored** skills (#29, `apps/skills.py`): a
`.skill/<name>/SKILL.md` with `name`+`description` frontmatter, indexed in the system prompt,
body loaded via `read_skill`. #298 = extend it from "dev writes it, baked in the package" to
"**user + AI write it, loaded at runtime from the workspace, portable, promotable**" — reusing
the existing format/parser, the workspace FileStore, the existing workspace file
download/upload API, and dogfooding the skill mechanism itself to drive the authoring flow.

## Locked decisions (grill Q1–Q10)

- **Q1** A co-created skill lives **per-workspace** in the FileStore `.skill/` (not a per-user
  library, not only baked-in). Reuses the `.skill/` convention + topic-hub's file-injection.
- **Q2** The authoring flow is a **meta-skill `author-skill`** (dogfood: a skill that teaches
  the agent to co-author skills), not a workflow or a dedicated App — extraction is a
  conversation; a workflow's single human-gate can't host a multi-turn interview.
- **Q3a** Skill discovery merges **package skills (`@cache`)** + **workspace `.skill/`
  (re-read every turn, *uncached*)** + shared skills (Q7). A skill written this turn appears in
  the index next turn (like context_files re-reading `MEMORY.md`).
- **Q3b** Add a deterministic **`save_skill(name, description, body)`** tool that owns the
  SKILL.md write (correct frontmatter, `name==dir`, right path, hard errors instead of the
  loader silently skipping a malformed hand-written file). `references/`/`scripts/` are written
  with the ordinary `write_file` (no format constraints).
- **Q4** v1 skill = **`SKILL.md` + `references/` + `scripts/`**. References ride the existing
  `read_file`; scripts ride the existing `exec` against the **python-stack** venv carrier
  (`exec(["python", ".skill/<name>/scripts/foo.py"])`). No new execution machinery.
  **Boundary:** skill scripts use only python-stack's frozen deps — **no installing new
  packages**. A skill that needs a custom dependency or a validated/reusable tool graduates
  into a real **tool-package** (§B of `plan-skills-and-tools.md`) — that's a later phase.
  *(Superseded on the install half: `pip` is now shimmed to the carrier, so a script CAN
  `pip install` into the same interpreter. What survives is the reason the boundary existed —
  such an install is unpinned, unreproducible and dies with the workspace, so a stable script
  with custom deps still graduates. See `docs/skills-authoring.md`.)*
- **Q5** Portability **rides the existing workspace file API**: export = `POST
  .../files/download/prepare?prefix=.skill/<name>` → zip; import = existing `PUT
  .../files/{path}`. No new export/import subsystem. v1 ships a **thin FE skills panel**
  (`.skill/` is a hidden dir the IDE tree won't show — without a surface the flow is invisible).
- **Q6** Promotion is **manual**: the user downloads the skill folder and hands it to a dev who
  commits it into `apps/<slug>/profiles/<p>/.skill/`. No "request promotion" button.
  Baked-in (package) skills stay **SKILL.md-only** in v1 (their `references`/`scripts` would
  need importlib reads / sandbox seeding — out of scope; promoting a script-bearing skill is a
  dev judgement: graduate the script to a tool-package or seed it as a profile `.tpl`).
- **Q7** Shared skills are introduced **exactly like tool-packages**: a `sample-skills/<name>/`
  source dir (mirrors `sample-tools/`), a `SHARED_SKILLS: dict[str, Path]` registry (mirrors
  `PACKAGES`; **no prebuild** — skills are plain markdown), and per-app opt-in via a new
  `app.json` `agent.skills: [...]` list (parallel to `agent.tools`). `save_skill` is listed in
  `agent.tools` like any tool; `read_skill` keeps its "auto-wire when skills exist" rule. v1
  opts in **rca / topic-hub / playground**.
- **Q8** `author-skill` SOP = six steps: (1) scope + when-to-use; (2) extract the **process,
  terminology, and style** — *also reading the workspace's existing artifacts*, not only Q&A;
  (3) draft standard-format SKILL.md (+ references/scripts as needed); (4) show + revise to
  approval; (5) `save_skill` (references/scripts via write_file); (6) close out — it's loadable
  now via `read_skill`, here's how to download/reuse/promote. A dry-run self-test is a *soft*
  suggested step.
- **Q9** Format stays minimal: frontmatter = `name` + `description` only (no version/author/
  tags — git + specstar metadata already carry those); `name` is kebab-case and equals the dir
  (`save_skill` slugifies + enforces); `references/`/`scripts/` are conventions, not validated;
  body cap stays 50k. Same parser as #29 — workspace + package + shared skills are one format.
- **Q10** DoD: `/tdd`; iterate on changed-behaviour tests + ruff/ty, then the **full suite +
  100% coverage gate** + **whole-project `ty`** at the end; **live small-model (Qwen) check** —
  a real run where the agent follows `author-skill`, calls `save_skill`, and the skill loads via
  `read_skill` next turn (fake-LLM tests prove wiring, not that the prompt carries a small
  model). FE follows `/tdd` (vitest).

## Architecture touchpoints (verified)

- `apps/skills.py` — `_parse_frontmatter`, `SkillMeta`, `list_skills`/`load_skill` (package,
  `@cache`, importlib). Extend with **workspace** discovery/load (via `WorkspaceFiles`) +
  **shared** discovery/load (via the registry). Reuse `_parse_frontmatter` + `SKILL_BODY_CAP`.
- `apps/catalog.py::_compose_prompt` / `resolve` — builds the "## Available skills" index from
  `list_skills`. Merge **shared** skills here (disk-readable, no filestore). Workspace skills
  are injected per-turn instead (they need the filestore).
- `apps/context_files.py` + `api/app.py` turn setup (`build_context_block`, ~L2450) — the
  per-turn injection point. Add a sibling **workspace-skills index block** built from `.skill/`.
- `agent/tools.py` — `read_skill_impl` (extend precedence: **workspace → shared → package**),
  new `save_skill_impl`, `_IMPLS["save_skill"]`, `build_tools` wires `read_skill` when package
  **or shared** skills exist. `AgentToolContext` already carries `files`, `investigation_id`,
  `app_slug`, `template_profile`.
- `apps/manifest.py::AgentManifest` — new `skills: list[str]` field; `validate_all_apps`
  rejects a name not in `SHARED_SKILLS`.
- `apps/shared_skills.py` (new) — `SHARED_SKILLS` registry (mirrors `tooling/packages.py`).
- `sample-skills/author-skill/SKILL.md` (new) — the meta-skill body.
- `web/` — thin skills panel (list/download/＋new/import) + a small list endpoint.

## Phases (flat, /tdd one slice per commit)

- **P1 — workspace skill discovery + per-turn index + `read_skill` reads workspace.**
  `apps/skills.py`: `workspace_skill_metas(files, inv)` (async, uncached, parse each
  `.skill/*/SKILL.md`) + `load_workspace_skill(files, inv, name)`. A `workspace_skills_block`
  renderer; inject it at the workspace turn setup beside context_files (prepended, not
  persisted). `read_skill_impl`: try workspace first, fall through to package. Tests: discovery
  (skips malformed like the package loader), uncached (sees a just-written skill), block render,
  read_skill workspace hit + miss lists union.
- **P2 — `save_skill` tool.** `save_skill_impl(name, description, body)`: slugify `name` →
  kebab, reject empty; collapse `description` to one line (the minimal YAML parser is
  line-based); assemble `---\nname:…\ndescription:…\n---\n\n<body>`; reject body > 50k; write
  `.skill/<slug>/SKILL.md` via `ctx.files` (overwrite allowed — authoring iterates); return
  "saved; load with read_skill('<slug>')". `_IMPLS["save_skill"]`. Tests: save→loadable (P1
  loader), name slugified + `name==dir`, over-cap rejected, multi-line description sanitised,
  re-save overwrites, no-workspace context → friendly error.
- **P3 — shared-skills registry + `agent.skills` field + index/tool wiring.**
  `apps/shared_skills.py` `SHARED_SKILLS = {"author-skill": <repo>/sample-skills/author-skill}`;
  `shared_skill_metas(names)` + `load_shared_skill(name)` (reuse `_parse_frontmatter`).
  `AgentManifest.skills`. `resolve` merges shared into the prompt index. `build_tools` wires
  `read_skill` when package **or** declared-shared skills exist. `read_skill_impl` precedence
  workspace → shared → package. `validate_all_apps` raises on an `agent.skills` name absent from
  the registry. Tests: registry meta/body load, manifest parse, resolve index includes shared,
  coherence raise, read_skill shared hit, build_tools wiring.
- **P4 — `author-skill` content + opt-in three apps.** Write `sample-skills/author-skill/
  SKILL.md` (the six-step SOP, English body, ≤50k). Add `"author-skill"` to `agent.skills` and
  `"save_skill"` to `agent.tools` in `rca`/`topic-hub`/`playground` `app.json`. Tests: each app's
  resolve advertises `author-skill`; each exposes `read_skill`+`save_skill`; scripted-runner
  e2e — agent calls `save_skill`, the skill loads via `read_skill` the next turn.
- **P5 — thin FE skills panel + list endpoint.** Backend: `GET /a/{slug}/items/{id}/skills` →
  typed `[{name, description}]` (parse workspace `.skill/*/SKILL.md`). FE (TanStack Query):
  a Skills panel listing workspace skills, each with **Download** (existing prefix-zip
  prepare→stream), **＋ New skill** (seeds a chat message that kicks off `author-skill`), and
  **Import** (pick a downloaded skill zip → expand client-side → `PUT` each member under
  `.skill/`). vitest TDD; English UI strings via `useT`.
- **P6 — docs + live check + gate.** `docs/skills-authoring.md` (the user-facing flow + the
  script→tool-package promotion note + the baked-in-skill scripts gap). Live small-model (Qwen)
  canned check (DoD). Full suite + 100% coverage + whole-project `ty` + ruff.

## Out of scope (v1)

Per-user / cross-workspace skill library (Q1 future phase); baked-in skills carrying
references/scripts (Q6); runtime pip installs / custom-dep skill scripts (Q4 → graduate to a
tool-package); a "request promotion" button / issue filing (Q6); skill versioning / cross-skill
`$ref` expansion (inherited from #29 A.3).
