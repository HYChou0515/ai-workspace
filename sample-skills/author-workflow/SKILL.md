---
name: author-workflow
description: Co-create a runnable workflow with the user — capture a repeatable, multi-step procedure as a workflow.json the platform can run headless. Use when the user wants to "make/save a workflow", to automate a recurring multi-step task (classify + file uploads, produce a report, digest then commit), or after a task you could turn into a repeatable, re-runnable procedure.
---

# Co-author a workflow with the user

A **workflow** is a repeatable, multi-step procedure the platform can run on its own
(headless, re-runnable, resumable) — *not* a one-off chat. You and the user design it
together; you save it as **data** (a `workflow.json`) with `save_workflow`; the user
can then **Run** it, **download** it to hand to the dev team, or have it promoted to a
default. Because it's data — not code — running it is safe.

This is the workflow analogue of `author-skill`. A *skill* captures *how* to do a kind
of task (you read it, then do the work). A *workflow* captures the steps *as runnable
units* the platform executes for you. If the user just wants to remember a method, make
a skill; if they want it **run**, make a workflow.

Keep the user in the loop — this is a conversation, not a form. Write in their language.

## 1. Agree on the job

Confirm in one or two sentences, and get a yes before drafting:

- **What recurring task** is this? Keep it to ONE job (one workflow, one outcome).
- **What goes in?** Usually files the user drops into `uploads/`. A workflow reads the
  workspace; it doesn't take a form.
- **What's the outcome?** A reviewed report? Files landed in a knowledge collection?
  Context cards written? The outcome decides the last step.

## 2. Know the building blocks

A workflow is an ordered list of **steps**. These are the only kinds (v1):

- **`agent`** — one LLM turn. It *reads / decides* and records its decision as a file.
  Give it read-only `tools` (e.g. `["read_file", "ask_knowledge_base"]`). Set `out` to
  the file it writes (it replies with the content; the step saves it). Every `agent`
  step needs a **gate** (`check`) unless it has an `out` (which gates on the file).
- **`sandbox`** — a shell command, no LLM. The escape hatch for custom deterministic
  work (parse, transform, compute). Compute only — it can't reach collections.
- **`gate`** — pause for the user to approve before anything irreversible. The standard
  shape is **produce → review → commit**: agents draft a plan (safe), the user approves,
  *then* a capability commits. A reject commits nothing.
- **`capability`** — a reliable action: `ingest_to_collection` (file into a KB
  collection) or `upsert_context_card`. These are the only steps that change anything
  outside the workspace, and they run as the user.
- **`map`** — repeat the inner steps over each file matched by a glob (`over`), binding
  each path to `as`. This is how you process "every uploaded file". One level deep.

**The golden rule (decision/action split):** the LLM only *decides* and writes its
decision to a file; a `capability` step does the irreversible *action*. Never give an
agent the power to commit — it drafts, the user approves, the capability commits.

### Passing values

Use `{...}` to fill in values — **no logic, just lookups**:

- `{config.X}` — a value from the workflow's own `config`.
- `{file}` (or whatever you named `as`) — the current file in a `map`.
- `{p.field}` — when `p` is a path to a `.json` file, reads that file and takes `field`.
  This is how a `commit` step uses the collection the `classify` agent chose:
  `"collection": "{p.collection}"`.

## 3. Draft, save, fix

Write the `workflow.json` and call `save_workflow(id, workflow_json)`. The shape:

```json
{
  "schema": 1,
  "id": "file-uploads",
  "title": "File uploads into a collection",
  "config": { "collections": ["notes"] },
  "phases": [
    { "id": "classify", "title": "Classify" },
    { "id": "review", "title": "Review" },
    { "id": "commit", "title": "Commit" }
  ],
  "steps": [
    { "type": "map", "over": "uploads/*", "as": "file", "phase": "classify", "do": [
      { "type": "agent",
        "prompt": "Read {file}. Choose a collection from {config.collections} and write a one-line digest. Reply with a JSON object with keys collection, digest, and source (set source to {file}).",
        "phase": "classify", "out": "plan/{file}.json", "tools": ["read_file"],
        "check": { "choice_in": { "path": "plan/{file}.json", "key": "collection", "allowed": "{config.collections}" } },
        "retries": 2 } ] },
    { "type": "gate", "phase": "review", "title": "File these into the collection?", "summary_from": "plan/*.json", "allow": ["approve", "reject"] },
    { "type": "map", "over": "plan/*.json", "as": "p", "phase": "commit", "do": [
      { "type": "capability", "call": "ingest_to_collection", "phase": "commit", "collection": "{p.collection}", "path": "{p.source}" } ] }
  ]
}
```

- Every step's `phase` must be one of the declared `phases`. Keep phases coarse and
  roughly linear — they're the progress diagram, not every step.
- `save_workflow` **validates** before saving. If it returns problems, **read each one
  and fix it** — don't re-save the same thing. Common ones: a `phase` you didn't
  declare, a `{variable}` that isn't `config` / `inputs` / a `map` var in scope, a
  capability or check name that doesn't exist, an `agent` step with neither `out` nor a
  `check`.

## 4. Hand it off

Tell the user they can **Run** it from this item to test it, **download** the
`.workflows` folder to reuse it in another workspace, or ask the dev team to **promote**
it to a built-in default. Don't try to "test-run" it yourself — pressing Run is theirs.

Keep it small. If the user describes two jobs, make two workflows. A workflow that needs
real branching, a revise loop, or heavy custom data-munging is past what this can express
— say so, and suggest the dev team build it as code instead.
