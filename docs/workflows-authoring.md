# Authoring a workflow

A practical guide for *writing* a workflow — the block catalog, the conventions,
and the tooling that keeps you out of the startup-crash loop. For the *why* (the
design, the decision/action split, the filesystem journal) read the spec,
[`workflows.md`](workflows.md); this is the how-to.

> TL;DR — `python -m workspace_app.workflow new <app> <profile> <id>` scaffolds a
> runnable workflow; edit its `run.py`; `python -m workspace_app.workflow check`
> tells you what's wrong before you boot the app.

## What a workflow is

A workflow is **one `async def run(wf, inputs)`** (the orchestration) plus a small
**data manifest** in the profile's `_profile.json` (its id, title, and the phase
skeleton the UI draws). The control flow is ordinary Python — `for` / `if` / `await`
— over a library of *steps*. There is no DSL.

It lives at the **profile** level:

```
apps/<app>/profiles/<profile>/
  _profile.json                       # declares the workflow (id, title, phases, …)
  workflows/<id>/run.py               # async def run(wf, inputs) — the orchestration
```

`run.py` is loaded by file path (so a hyphenated dir works) — use **absolute
imports** (`from workspace_app.workflow import ...`), never relative ones.

```python
from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_write_step
from workspace_app.workflow.handle import WorkflowHandle


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    await agent_write_step(wf, phase="note", out="note.md", prompt="Write a hello note.")
    return {"status": "done"}
```

`run()` returns a JSON-able summary (becomes the run's result). `inputs` is the
parsed `input.json` (see [Inputs](#inputs)).

## Quickstart

1. **Scaffold** a starting point — pick the recipe closest to what you want:

   ```bash
   uv run python -m workspace_app.workflow new myapp default ingest-logs --recipe review-commit
   ```

   Recipes: `minimal` (one agent step, runs to *done*), `review-commit` (produce →
   human gate → commit, runs to *awaiting_human*), `batch` (`wf.map` over uploads).
   It writes an annotated `run.py` and registers it in `_profile.json` with phases
   that already match the code.

2. **Edit** `run.py` — change prompts, add steps, wire your commit.

3. **Check** before you boot:

   ```bash
   uv run python -m workspace_app.workflow check        # all apps
   uv run python -m workspace_app.workflow check myapp  # one app
   ```

   `check` statically reports a missing/`run()`-less/unparseable `run.py`, empty or
   duplicate ids, empty phase ids (**errors**), and a `phase="…"` literal in your
   code that you forgot to declare in `_profile.json` (**warning** — the drift/typo
   case). It exits non-zero on any error, so it's a good pre-commit hook.

4. **Restart** the app — workflows are discovered at boot.

## The block catalog

Everything below is imported from `workspace_app.workflow` unless noted. `wf` is the
[`WorkflowHandle`](#the-wf-handle).

### Agent nodes (LLM)

An agent node runs **one gated LLM turn** on the item — it streams into the chat
like an interactive turn. A gate is mandatory: an ungated agent node is not
expressible.

```python
await agent_step(wf, *, prompt, phase, check, name=None, key="",
                 tools=None, retries=0, cache=True) -> Any
```
The general form. `check` is a required postcondition (see [Gates](#gates));
`retries` re-runs with the failure reason fed back into the prompt. `tools` is the
agent's allowed tool subset (⊆ the profile ceiling).

```python
await agent_write_step(wf, *, prompt, phase, out, name=None, key="",
                       tools=None, retries=0, cache=True, check=None) -> Any
```
The common shorthand: the model **produces the file's content as its reply** (it
does *not* call `write_file` — small *and* large models emit long tool args
unreliably), and the step writes it to `out`, gated on `file_nonempty(out)` by
default. Give it **read-only** tools.

### Deterministic nodes (no LLM)

```python
await sandbox_node(wf, *, run, phase, check=None, name=None, key="", cache=True)
    -> {"exit_code": int, "stdout": str}
```
Runs a command in the sandbox. No LLM; this is plain author code. Use it for
reliable, scriptable work; gate it if its success isn't self-evident.

### Human gate

```python
decision = await human_gate(wf, *, phase, title, summary="",
                            allow=("approve", "reject")) -> Decision  # .choice, .input
```
Pause for a person. On first reach the run suspends as `awaiting_human`; once a
decision is recorded, a re-run replays the completed steps, reaches the gate, finds
the decision, and continues. `summary` is what the human reviews (a string or any
JSON-able value). This is the canonical **produce → review → commit** seam.

### Capabilities (deterministic side effects on `wf`)

These are the reliable, journaled, idempotent side effects — the *action* half of
the decision/action split. The agent never holds these; your `run()` calls them
after a gate.

```python
await wf.ingest_to_collection(collection, path, *, phase="ingest", cache=True) -> doc_id
await wf.upsert_context_card(collection, keys, *, title="", body="",
                             phase="commit", cache=True) -> card_id
await wf.find_overwrite_card(collection, keys, *, title="") -> {...} | None  # read-only
```

### Gates

A gate is a postcondition `async (wf, result) -> CheckResult`. Built-ins:

```python
file_nonempty(path)                       # the agent actually wrote a non-empty file
choice_in(path, *, key, allowed)          # path[key] ∈ allowed (clamp an agent's pick)
collection_has(collection, path)          # the ingest really landed the doc as ready
```
Write your own when needed — return `CheckResult(True)` or
`CheckResult(False, "why")`; the reason is fed back into the agent's retry.
`fail("reason")` aborts the current step/element (`StepFailed`).

### The `wf` handle

File IO (workspace-relative paths; leading `/` optional):

```python
await wf.read(path) / read_text(path) / read_json(path)
await wf.write(path, data) / write_json(path, obj)
await wf.exists(path) / delete(path)
await wf.glob(patterns, exclude=None) -> [paths]      # sorted, deterministic
```

Parallel for-each (manual §11):

```python
failures = await wf.map(fn, items, *, concurrency=8)  # skip+collect; returns [{item, error}]
```

Context: `wf.config` (the manifest's `config`), `wf.user` (captured actor),
`wf.upload_dir` (the profile's staging folder, default `uploads`), `wf.workflow_id`,
`wf.journal_dir`.

### Engine primitive

`run_step(wf, *, name, key="", phase="", args, execute, check=None, retries=0,
cache=True)` is what the adapters above are built on. Reach for it directly only for
a custom deterministic node you want journaled + phase-emitting (e.g. a commit that
isn't one of the capabilities).

## Conventions that matter

- **Pass a step's inputs as its arguments.** The step's cache key is
  `hash(args)` — so editing an upstream artifact changes a downstream arg and
  re-runs it automatically. Don't read ambient state inside a step.
- **Keep `phase=` a literal.** It must match a phase declared in `_profile.json`
  (that's what `check` cross-checks). Put the dynamic part of a step's identity in
  `name=` / `key=`, not `phase=`. Phases should be coarse and mostly linear — they
  are the progress diagram, not every step.
- **The filesystem is the journal.** Each step writes
  `/.workflow/<id>/step_<name>/<key>.json`. A re-run skips a step whose artifact
  exists with a matching input-hash. To force a re-run, edit/delete the artifact and
  press Run; `cache=False` always re-runs. There is no rewind API — editing files
  *is* the intervention.
- **Decision/action split.** The LLM only *decides* and writes its decision as data;
  the reliable side effect (`ingest_to_collection`, `upsert_context_card`, your
  `sandbox_node`) is a deterministic node the agent never holds a tool for. Stronger
  than a post-hoc gate.
- **Tools an agent node may hold.** When you list `tools=` for an `agent_step`, give
  an app/workflow agent **`ask_knowledge_base`** to consult the KB (it delegates to a
  KB sub-agent and keeps the noisy retrieval out of your context). **Never** list
  `kb_search` / `search_wiki` in an app workflow — those are the KB/wiki agents' own
  retrieval leaves and need a retriever the app doesn't have (they'll fail). The one
  exception is `lookup_glossary` — a cheap, deterministic, exact-key card lookup you
  may grant directly (#270).

## Inputs

The platform surfaces exactly one input file to `run()`: `input.json`, at
`{upload_dir}/input.json` by default (override with the manifest's `input_json`).
Its *shape* is your workflow's business — the platform doesn't validate it. Read it
with `await wf.read_json("uploads/input.json")` (or whatever your manifest points
at), or rely on `inputs` if the driver passed it in. A profile seeds a starter
`input.json` like any other starter file.

## The manifest

```jsonc
{
  "workflows": [
    {
      "id": "ingest-logs",                 // stable, unique; addresses run.py + the picker
      "title": "Ingest logs",              // shown in the Run picker
      "tag": "batch",                      // a small kind pill (batch | single | …)
      "description": "…",                  // one line on the launcher card
      "hint": "Drop files into uploads/.", // one-line inputs hint
      "phases": [                          // the read-only progress skeleton (manual §12)
        { "id": "classify", "title": "Classify" },
        { "id": "commit", "title": "Commit" }
      ]
    }
  ]
}
```

Every `phase="…"` literal your `run.py` emits should appear in `phases`. The
scaffold keeps them in sync for you; `check` warns when they drift.

## Recipe gallery

The scaffold's three starting shapes — all `check`-clean out of the box:

| Recipe | Shape | Runs to |
| --- | --- | --- |
| `minimal` | one `agent_write_step` | `done` |
| `review-commit` | produce → `human_gate` → deterministic commit | `awaiting_human` (approve to finish) |
| `batch` | `wf.map` over `uploads/*`, one agent node per file | `done` |

Read the generated `run.py` — it is annotated and is the fastest way to see a block
in context. The bundled `apps/topic-hub/profiles/default/workflows/` (memory,
collections, consolidate) are fuller real examples.

## Troubleshooting

- **It crashed at startup.** Run `check` — it names the file and the fix. (Boot also
  `exec`s `run.py`, so it additionally catches import / `NameError` failures a static
  `check` can't — read the traceback for those.)
- **The progress diagram is wrong / a phase never lights up.** A `phase=` literal in
  your code isn't declared (or vice versa). `check` warns on the former.
- **A step won't re-run after I changed a prompt.** It should — the prompt is in the
  input-hash. If you changed an *artifact* the step reads, that re-runs it too. To
  force it, delete the `step_*` artifact or pass `cache=False`.
