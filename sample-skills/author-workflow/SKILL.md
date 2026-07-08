---
name: author-workflow
description: Co-create a runnable workflow with the user — capture a repeatable, multi-step procedure as a workflow.json the platform can run headless. Use when the user wants to "make/save a workflow", to automate a recurring multi-step task (classify + file uploads, produce a report, digest then commit), or after a task you could turn into a repeatable, re-runnable procedure.
---

# Co-author a workflow with the user

A **workflow** is a repeatable, multi-step procedure the platform runs on its own
(headless, re-runnable, resumable) — *not* a one-off chat. You design it WITH the user and
save it as **data** (a `workflow.json`) with `save_workflow`; they can then **Run** it,
**download** it to hand to the dev team, or have it promoted to a default. Because it's
data — not code — running it is safe.

This is the workflow analogue of `author-skill`. A *skill* captures *how* to do a task
(you read it, then do the work). A *workflow* captures the steps *as runnable units* the
platform executes. If the user just wants to remember a method → make a skill; if they
want it **run** → make a workflow.

Keep the user in the loop — this is a conversation, not a form. Write in their language.

## The idea that makes a workflow reliable

**Decision / action split.** An agent step only *decides* and records a verified output; a
**capability** step performs any irreversible *action*. Never give an agent the power to
commit — the standard shape is **produce → review → commit**: agents draft (safe), a `gate`
lets the user approve, and only *then* a capability commits. A reject commits nothing.

Two consequences you design around:

- **Every step is verified before the next one runs.** An agent produces *either* a
  structured decision (`outputs`) *or* a prose artifact (`out` + a declared `kind`), and the
  platform checks it — a malformed or missing output fails and retries; it never flows
  downstream unchecked. So a later step can trust what an earlier one produced. (For a prose
  artifact you can also declare `requires` — required sections / minimum length.)
- **Side-effects go only through a `capability`.** A `sandbox` step is compute-only; filing
  into a collection, writing a card, or creating an entity is always a capability.

## How to author

1. **Agree on the job** — one recurring task, one outcome. What goes in (usually files the
   user drops into `uploads/`)? What is the outcome (a reviewed report? files landed in a
   collection?) — the outcome decides the last step. Get a yes before drafting.
2. **Draft the `workflow.json`.** Its exact grammar — every step `type` and its fields, how
   to reference an earlier step's output, and what *this app's* steps may use — is in the
   **machine-derived reference appended below**. It is generated from the live schema, so it
   is always current: use it, don't guess the fields from memory.
3. **Save it** with `save_workflow(id, workflow_json)`. It **validates before saving** — if it
   returns problems, read each one and fix it; don't re-save the same thing. The validator is
   the guarantee: an invalid workflow cannot be saved.
4. **Hand off.** Tell the user they can **Run** it from this item to test it, **download** the
   `.workflows` folder to reuse elsewhere, or ask the dev team to **promote** it. Don't
   "test-run" it yourself — pressing Run is theirs.

Keep it small. Two jobs → two workflows. A workflow that needs unbounded loops, arbitrary
branching, or heavy custom computation is past what this expresses — say so, and suggest the
dev team build it as code (`run.py`).
