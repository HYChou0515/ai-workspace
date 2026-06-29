# Plan — #323 user-authored workflows (DSL)

> Status: grill-locked design (Q1–Q9 in the conversation; mirrors the #298 skill
> model). This is the build plan; the spec lives in
> [`workflows.md` §22](workflows.md). Flat integer phases (CLAUDE.md). **P1–P3
> shipped (PR #332); P4–P5 shipped (the second PR)** — full v1 is now in.

## Why

#323 = *do for workflows what #298 did for skills*. Let a user co-design a
workflow **with the AI** (like a skill), save it, download it to hand off, and
have a dev **promote** it to a profile default. The twist a skill doesn't have:
a workflow **executes**. So the user can't author **code** — running user Python
in the API is unsafe (the orchestration `run()` is trusted backend Python that
holds the turn engine / sandbox lifecycle / capability credential, manual §1).

## Locked design (grill)

| # | Decision |
| --- | --- |
| Q1 | **Tiered**: user self-serves a *constrained/downgraded* workflow that runs, and the good ones promote to a trusted default. |
| Q2 | **Declarative data + trusted interpreter** (a `workflow.json` "downgraded DSL"). No user code runs in the API. |
| Q3 | **Skeleton DSL** (sequence / map / agent / gate / capability) + **`sandbox_node`** as the one escape hatch for arbitrary deterministic logic (runs in the sandbox). Branch via data-routing (§11). |
| Q4 | **Author-equals-hand invariant**: a user workflow does exactly what its author could do by hand. Capabilities are DSL primitives run under the captured user's authz; a user `sandbox` step is compute-only (no run-scoped credential). |
| Q5 | **Mirror the skill model**: a workspace workflow lives at `<workspace>/.workflows/<id>.json` (FileStore), item-local, live-read, shadows a package workflow of the same id, downloadable via the generic folder download, dev-promoted. **Not** a specstar resource. |
| Q6 | **One interpreter serves both tiers**: a *package* workflow can be `run.py` (trusted Python) **or** `workflow.json` (interpreted DSL). Promote = copy the json into the profile; no transpile. |
| Q7 | **v1 expressiveness ceiling**: sequence + map (one level) + agent + sandbox + capability + single-shot gate (approve/reject) + declarative checks + template interpolation. **No** revise-loop / branch / nested map; custom deterministic logic → a `sandbox` step. Covers `memory`/`consolidate` fully; `collections`-style polish stays dev-Python. |
| Q8 | **Co-design = draft + validate at save** (`save_workflow` runs the validator, rejects with a reason the AI fixes). No auto-test-run in v1; the user presses Run to test. |
| Q9 | **Same caps as package workflows** (existing per-run timeout / max-steps / concurrency, manual §16–§17). Authz = item access. Captured-user scope. |

## The DSL (`workflow.json`)

```jsonc
{
  "schema": 1,
  "id": "ingest-logs",
  "title": "File uploads into collections",
  "phases": [
    { "id": "classify", "title": "Classify" },
    { "id": "review",   "title": "Review" },
    { "id": "commit",   "title": "Commit" }
  ],
  "config": { "collections": ["logs", "specs"] },
  "steps": [
    { "type": "map", "over": "uploads/*", "as": "file", "phase": "classify", "do": [
      { "type": "agent",
        "prompt": "Read {file}. Pick a collection from {config.collections}; write a digest. Output JSON {collection, digest, source}.",
        "out": "plan/{file}.json",
        "tools": ["read_file", "ask_knowledge_base"],
        "check": { "choice_in": { "path": "plan/{file}.json", "key": "collection", "allowed": "{config.collections}" } },
        "retries": 2 } ] },
    { "type": "gate", "phase": "review", "title": "Approve filing these?", "summary_from": "plan/*.json", "allow": ["approve", "reject"] },
    { "type": "map", "over": "plan/*.json", "as": "p", "phase": "commit", "do": [
      { "type": "capability", "call": "ingest_to_collection", "collection": "{p.collection}", "path": "{p.source}" } ] }
  ]
}
```

- **Step types** (`type` is the msgspec tag): `agent`, `sandbox`, `gate`, `capability`, `map`.
- **Interpolation** `_resolve(template, ns)` — deterministic, async, **no eval**.
  Namespace = `{config, inputs}` + the active map var. `{x}` → the bound value;
  `{x.y}` → index a dict; **`{x.y}` where `x` is a path to a `.json` file → read
  + parse + index** (this routes the agent's recorded decision to a capability —
  the §8 decision/action split). A template that is exactly `{expr}` returns the
  resolved value (may be a list); otherwise it string-substitutes.
- **`map`** binds `as` to each matched path (sorted glob → deterministic step
  identity, §9). One level; no nesting (Q7).
- **Capabilities** (v1): `ingest_to_collection`, `upsert_context_card`. Run under
  the captured user (Q4). A `sandbox` step gets **no credential** (compute-only).
- **Checks** (declarative): `file_nonempty`, `choice_in`, `collection_has`.

The interpreter is a trusted `ProfileRun` (`async def run(wf, inputs)`) that
walks `steps` and dispatches to the **existing** `agent_step` / `sandbox_node` /
`human_gate` / `wf.ingest_to_collection` / `wf.map` primitives. The §9
filesystem-journal + input-hash skip works unchanged: the DSL is fixed data and
interpolation is deterministic, so each step's identity is stable across re-runs.

## Phases

- **P1 — DSL engine** (`workflow/dsl.py`). Schema (msgspec tagged union),
  `build_run(def) -> ProfileRun`, `build_manifest(def) -> WorkflowManifest`,
  `validate_def(def, *, tool_ceiling, capabilities) -> list[str]`, async
  `_resolve` interpolation. Unit-tested against a fake `WorkflowHandle`.
- **P2 — package-tier discovery**. `discovery.load_run_callable` runs a
  `profiles/<p>/workflows/<id>/workflow.json` (the interpreter) in place of a
  `run.py` (Q6 — JSON wins when both exist); `authoring._check_workflow` validates
  it via `validate_def` so the bundled-clean CI gate + boot fail loud on a bad one.
  This makes the interpreter runnable in production and is the **promote target**.
  *Declaration-driven for v1:* a package DSL workflow is still listed in
  `_profile.json` `workflows: [...]` like any other (so discovery/the Run picker are
  untouched), so promote = drop the json **and** add the one-line `_profile.json`
  entry. A self-describing drop-in scan (no `_profile.json` edit, the fuller
  skill-parity) is a follow-up. Living example: `apps/playground/profiles/dsl`.
- **P3 — authoring**. `save_workflow` agent tool (validate → write
  `<ws>/.workflows/<id>.json`) mirroring `save_skill`; `author-workflow` shared
  meta-skill (`sample-skills/author-workflow/SKILL.md`) opted-in via `app.json`
  `agent.skills` (topic-hub is the first adopter); workspace-workflow listing
  (`workspace_store.workspace_workflow_metas`, for the panel + P4 discovery). The
  tool-ceiling clamp (Q4) is enforced at save (`_profile_tool_ceiling`). Co-design
  + export (generic folder download) + dev-promote — the #298 skill flow, for
  workflows. (The Workflows-panel **route** + FE land with P5.)

### P4 / P5 (shipped — second PR)

- **P4 — workspace self-serve run** ✅. An injected `load_workspace(item_id,
  workflow_id) → (run, manifest) | None` on the orchestrator resolves a
  `<ws>/.workflows/<id>.json` (the interpreter + its manifest), shadowing a
  package workflow of the same id (Q5); `_resolve_run` / `_resolve_manifest` try
  it first on every reach (start + each resume) and fall back to the package
  `load_run` / `load_manifest` (default `None` ⇒ existing behaviour unchanged, so
  no existing test moved). The Run-route `_workflow_manifest_or_404` gains the
  same workspace fallback (no 422 for a workspace id), and a new item-scoped
  `GET /a/{slug}/items/{item_id}/workflows` lists them. `workspace_store.load_workspace_workflow`
  is the one read both share. So a user saves a `workflow.json` and presses Run —
  the existing Run endpoint / orchestrator / journal / gate machinery runs it.
- **P5 — FE Workflows panel** ✅. `WorkflowsModal` (mirrors `SkillsModal`):
  lists the workspace workflows with a per-row **Run** button (the self-serve
  trigger via `workflowApi.startRun`), a **Download all** (`.workflows/` folder
  zip) and **Import** (`.json` → `.workflows/`). `useWorkspaceWorkflows` +
  `api/workspaceWorkflows` + a `workspaceWorkflows` query key + `workflows.*`
  i18n; opened from a header button in `AgentPanel` next to Skills.

### Deferred / non-goals (v2)

revise-loop / branch / nested map; auto-test-run during co-design (make_deck
style); transpile-to-Python on promote; per-user quota.
