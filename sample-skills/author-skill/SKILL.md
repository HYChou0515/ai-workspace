---
name: author-skill
description: Co-create a reusable skill with the user — capture their analysis flow, terminology, and preferred style as a loadable SKILL.md. Use when the user wants to "make/save a skill", asks you to remember how they like a task done, or after a task you could turn into a repeatable procedure.
---

# Co-author a skill with the user

A **skill** is a short, reusable instruction file the agent loads on demand. It
captures *how the user wants a kind of task done* — the steps of their analysis
flow, the terminology they use, and the output style they prefer — so next time
the work follows their way without re-explaining. Skills you save here load with
`read_skill` and can be downloaded and reused in another workspace.

Follow these steps. Keep the user in the loop — this is a conversation, not a
form. Write in the user's language.

## 1. Agree on scope and trigger

Ask (or confirm if it's already obvious from the conversation):

- **What task** is this skill for? Keep it to ONE kind of task — one skill, one
  job. If they describe two, make two skills.
- **When should it fire?** This becomes the one-line `description` — a concrete
  "use this when…" so the agent (later) knows to reach for it. Prefer the user's
  own trigger words.

State the scope back in one sentence and get a yes before drafting.

## 2. Extract the substance

Pull out three things — this is the valuable part, so dig:

- **Process** — the ordered steps the user follows. Number them. Note decision
  points ("if X, then…") and what "done" looks like.
- **Terminology** — domain words, abbreviations, and what they mean *here*.
- **Style** — the shape of a good result: sections, tone, length, what to lead
  with, what to leave out.

Don't rely only on Q&A. **Read what's already in this workspace** — the files,
the artifacts, and earlier messages in this chat often *show* the flow the user
just walked. Use `list_files` / `read_file` to mine a worked example, then
confirm "is this how you'd always want it?" Ask one focused question at a time;
stop when you can write the procedure without guessing.

## 3. Draft the SKILL.md body

Write the methodology as clear markdown the *future* agent will follow:

- Numbered steps mirroring the user's process; imperative voice ("Compute…",
  "Group by…", "Report…").
- A short terminology list if the task has jargon.
- A "what good output looks like" note (or a tiny example).
- Keep it tight — a skill is guidance, not an essay. If it grows past a few
  screens, split it into two skills.

If the task benefits from **reference material** (a long term table, a checklist,
a worked example), put it in a separate file under the skill's folder and point
to it from the body, e.g. "see `references/glossary.md`". The future agent reads
it with `read_file` only when needed.

If a **repeatable computation** is part of the flow, write a small Python script
under the skill's `scripts/` folder and document how to run it, e.g.
`exec(["python", ".skill/<name>/scripts/summarise.py", "data.csv"])`. Scripts use
the workspace's bundled Python stack (pandas / numpy / scipy / matplotlib) — do
**not** assume any other package can be installed.

## 4. Review with the user

Show the draft (the description + the body, and any reference/script files).
Ask what's wrong or missing. Iterate until they approve. Do **not** save an
unreviewed skill.

## 5. Save it

Once approved, call `save_skill(name, description, body)`:

- `name` — a short title; it's slugified to kebab-case automatically.
- `description` — the one-line trigger from step 1.
- `body` — the markdown from step 3.

`save_skill` owns the file format, so you only pass those three fields. For any
reference or script files, write them with `write_file` into the same
`.skill/<name>/` folder (e.g. `.skill/<name>/references/glossary.md`,
`.skill/<name>/scripts/summarise.py`) and make sure the body points to them.

**Optional dry run:** if it's quick, load the new skill with `read_skill(<name>)`
and walk it once on real data so the user can confirm it produces what they want
before relying on it.

## 6. Close out

Tell the user, plainly:

- The skill is saved and loads any time with `read_skill('<name>')`.
- To reuse it in another workspace, download its folder from the Skills panel and
  import it there.
- If it proves stable and they want it built in for everyone, they can download
  it and ask the team to add it to the starting profile.
