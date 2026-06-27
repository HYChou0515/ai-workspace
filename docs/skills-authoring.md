# Co-creating skills (#298)

A **skill** is a short, reusable instruction file the agent loads on demand. It
captures *how you want a kind of task done* — the steps of your analysis flow, your
terminology, and your preferred output style — so next time the work follows your
way without re-explaining. This builds on the dev-authored skill mechanism (#29):
same `SKILL.md` format and `read_skill` progressive disclosure, now **user + AI
co-created at runtime, portable, and promotable**.

## The flow (what happens)

In any workspace app (RCA, Topic Hub, Playground), just tell the assistant you want
to make a skill — e.g. *"help me make a skill for triaging reflow defects"*. The
agent loads the built-in **`author-skill`** meta-skill and walks a six-step flow:

1. **Scope + trigger** — what one task is this for, and when should it fire (the
   skill's one-line description).
2. **Extract** — your process (the ordered steps), terminology, and output style.
   It also reads what's already in the workspace (files, earlier messages) to mine
   a worked example instead of relying only on questions.
3. **Draft** — a standard-format `SKILL.md` body.
4. **Review** — it shows you the draft and iterates until you approve.
5. **Save** — it calls `save_skill`, which writes `.skill/<name>/SKILL.md` with
   correct frontmatter (you never hand-edit the file).
6. **Close out** — the skill now loads with `read_skill('<name>')`; it tells you how
   to download/reuse/promote it.

## Where a skill lives

Per-workspace, in the FileStore at `.skill/<name>/`:

```
.skill/
  triage-reflow/
    SKILL.md           # frontmatter (name + description) + the methodology
    references/        # optional — extra docs the agent reads on demand
      defect-glossary.md
    scripts/           # optional — small Python the agent runs via exec
      summarise.py
```

- **References** are just files the agent reads with `read_file` when the body
  points at them (`see references/defect-glossary.md`). No special handling.
- **Scripts** run via the workspace's bundled Python stack —
  `exec(["python", ".skill/<name>/scripts/summarise.py", "data.csv"])` — which
  carries pandas / numpy / scipy / matplotlib. **A skill script cannot install new
  packages**; if it needs a custom dependency or you want a validated, reusable
  tool, that's the moment to graduate it into a proper tool-package (see
  `docs/plan-skills-and-tools.md` §B).

A skill you save loads in the same workspace immediately (the index refreshes each
turn). It does **not** leak into other workspaces — that's what download/import is
for.

## The Skills panel

The IDE file tree hides the `.skill/` dot-folder, so the **Skills** button in the
chat header opens a panel that lists this workspace's skills. From there you can:

- **Download** a skill as its folder zip — to reuse it elsewhere, or to hand to the
  team to bake into the starting profile.
- **Import** a skill folder you downloaded earlier into this workspace.

## Reusing a skill elsewhere

1. Download the skill from the Skills panel (a zip of its `.skill/<name>/` folder).
2. Unzip it locally.
3. In another item's Skills panel, **Import** the unzipped folder. It loads there
   immediately.

## Promoting a skill into the starting profile

Once a skill is stable and you want it built in for everyone on an app:

1. Download its folder from the Skills panel.
2. Hand it to the team — a developer commits it into
   `apps/<slug>/profiles/<profile>/.skill/<name>/`, and every new workspace of that
   profile ships with it.

Note: a **baked-in** profile skill is read-only package content, so in v1 it carries
only its `SKILL.md` body — not workspace-mounted `references/`/`scripts/`. When
promoting a skill that has a script, the developer decides whether the script should
graduate into a tool-package or be seeded into the workspace as a profile template
file. (Workspace skills you create have no such limit — their references and scripts
work because they live in the workspace the sandbox mounts.)

## For app authors

Built-in skills are introduced like tool-packages (see
`docs/plan-issue-298.md` Q7): add the source under `sample-skills/<name>/`, register
it in `workspace_app.apps.shared_skills.SHARED_SKILLS`, and opt an app in by listing
the name in its `app.json` `agent.skills` (and granting `save_skill` in
`agent.tools`). `author-skill` itself is shipped this way and opted into by RCA,
Topic Hub, and Playground.
