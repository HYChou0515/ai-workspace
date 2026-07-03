# $title

A file-first project. Everything here is a plain file you can read, edit, and
version — the app just gives you nicer ways to look at it.

## Layout

- `issues/` — one Markdown file per issue (`issues/1.md`, …). YAML frontmatter
  carries the typed fields (`title`, `status`, `assignee`, `due`, `progress`,
  `milestone`); the body is free-form notes.
- `milestones/` — one file per milestone. Each rolls up the progress of the
  issues that reference it.
- `views/` — declarative projections:
  - `table.ai.yaml` — every issue in a grid.
  - `board.ai.yaml` — issues grouped into status columns.
  - `gantt.ai.yaml` — issue date spans on a timeline, grouped by milestone.
  - `roadmap.ai.yaml` — milestones on a timeline.
- `.entity/` — the schemas + skeletons that define the two entity types (the app
  reads these to build the quick-create forms and validate records).

Create issues from the quick-create form or by asking the agent; new records get
the next number automatically.
