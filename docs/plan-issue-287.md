# Plan — #287 Workflow authoring DX (`scaffold` + `check` + guide)

Split from #283 (which keeps the *operator* axis: launch entry + progress viz).
This issue is the **author / setup** axis: a developer finds it hard to write or
modify a workflow inside the framework, and mistakes only fail loud at startup.

Grill-locked decisions (the consensus reached before building):

- **Core = the developer-in-repo authoring experience (axis A).** The operator
  config UI (forms over `collections.json` / `uploads/input.json`) is **out of
  scope** — `collections.json` already has a picker (#142) and `input.json` is
  deliberately profile-owned freeform (workflows.md §14).
- The four pains, all real: (1) don't know what blocks/API exist, (2) don't know
  how to start (blank `run.py`), (3) manifest `phases` drift from `run.py`, (4)
  mistakes only fail at startup with unclear messages.
- **v1 = scaffold + guide, mutually reinforcing.** The scaffold gives a runnable,
  annotated starting point ("how to start"); the guide is the block catalog +
  conventions ("what blocks exist"), authored to be read by **both a human and an
  AI assistant** (CLAUDE.md points at it).
- **Phase drift is low-impact / nice-to-have** (the author's call). Not a
  centrepiece: the scaffold writes `phases` to match the recipe, and `check`
  surfaces a *warning* on a `phase="literal"` in `run.py` that isn't declared
  (the typo case). It does **not** fail boot and does **not** flag declared-but-unused.
- Delivery = a **module CLI** `python -m workspace_app.workflow {new,check}` (same
  shape as `python -m workspace_app`).
- The guide is a **new `docs/workflows-authoring.md`**, cross-linked from
  `workflows.md`, with a one-line pointer added to `CLAUDE.md`. **No dedicated
  skill** (over-build for v1).

## Design

Purely **additive** — no change to existing workflow logic. New modules under
`src/workspace_app/workflow/`:

- `authoring.py` — `Diagnostic` + `check_app(slug)` / `check_profile_dir(dir, where)`.
  **Fully static** (AST, no `exec`): detects a missing/`run()`-less / syntactically
  broken `run.py`, empty/duplicate list-form workflow ids, empty phase ids
  (errors), and a `phase="literal"` used in `run.py` but not declared (warning,
  the drift/typo case). Non-literal `phase=expr` is skipped (can't verify); a
  declared-but-unused phase is **not** flagged (bundled fixtures legitimately
  declare phases they don't emit).
  - `check` is static *on top of* boot validation, not a replacement.
    `discovery.validate_workflow_profiles` stays as-is at startup: it `exec`s
    `run.py`, so it still catches import/`NameError` failures static parsing can't.
- `scaffold.py` — `scaffold_workflow(apps_dir, slug, profile, id, recipe, force)`.
  Recipes: `minimal` (one `agent_write_step`, runs to **done**), `review-commit`
  (produce → `human_gate` → deterministic commit, runs to **awaiting_human**),
  `batch` (`wf.map` over `uploads/*`). Writes the annotated `run.py` + creates /
  appends the `_profile.json` `workflows` entry with `phases` matching the recipe.
  Refuses to overwrite an existing id (unless `--force`) or to target a
  legacy-singular-`workflow` profile (would shadow it).
- `cli.py` — `main(argv) -> int`: dispatch `check` / `new`, print diagnostics /
  created files, exit non-zero on any error. (Tested directly.)
- `__main__.py` — two lines (`from .cli import main; raise SystemExit(main())`),
  added to the coverage `omit` list like the top-level `__main__.py`.

## Phases (flat; per CLAUDE.md)

- **P1** `check` core — `authoring.py` (Diagnostic + static `check_*`). TDD.
- **P2** module CLI — `cli.py` `main()` (`check [slug]`, exit codes) + `__main__.py`. TDD.
- **P3** scaffold `new` — `scaffold.py` recipes + `_profile.json` append; generated
  output must pass `check`. CLI `new` wired. TDD.
- **P4** gate test — every bundled app is diagnostic-clean (`check_app(slug) == []`),
  so CI catches drift. TDD.
- **P5** guide — `docs/workflows-authoring.md` (run() contract, full block catalog
  incl. `wf.*` methods, conventions, recipe gallery, how to run `new`/`check`) +
  `CLAUDE.md` pointer + `workflows.md` cross-link.
- **P6** live-check (DoD) — scaffold a throwaway workflow into `playground` and
  actually run it against the running app + Ollama: `minimal` → done,
  `review-commit` → awaiting_human.

## Out of scope (explicit)

Operator config UI (axis B) · visual / DAG authoring · runtime phase *enforcement*
(static check only) · `input.json` schema validation (profile-owned freeform) ·
hot-reload of newly scaffolded workflows (boot-time discovery is fine).
