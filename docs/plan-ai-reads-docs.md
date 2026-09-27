# Plan: the in-app AI reads our docs (readonly skill)

## Problem

A user whose mental model differs from how the system actually works will complain, and the in-app AI
cannot correct them: it does not know the system's design.

An audit (2026-09-27) of the workflow system is one example of the gap:

- the agent has only the `author-workflow` skill, whose DSL grammar keeps each step's first sentence;
- no system prompt mentions workflows;
- `docs/` is not in the image (`docker/Dockerfile` copies `src/`, `sample-skills/`, `sample-workflows/`,
  `view-plugins/`, not `docs/`);
- the /help chat reads only `getting-started.md` and the changelog.

The user's words: 「如果使用者心智模型跟系統不符合 一定會有超多抱怨 ai應該要能知道系統的設計是什麼」.

## Decisions (grill-me, 2026-09-27)

Each decision is tagged with its source: [user] for the user's call, [mine] for my own, open to override.

1. **The whole system**, front end and back end, not workflows alone [user: 「當然是做全部」].
2. **The AI reads our docs** as they are [user: 「其實就是要能看到我們的docs」]. There is no second, rewritten
   "user version" to keep in sync. The AI puts what it reads in the user's terms.
3. **Plans are read too, as long as they are not overturned.** The current docs say how the system works;
   the plans hold the design decisions and why they were made, which is where the mental model lives
   [user: 「沒過期的計劃才是設計與心智模型所在」].
4. **An overturned plan carries the id of the plan that overturned it** [user]. It does not list which
   decisions were overturned: an AI that reads both can tell which one is current. The one failure to
   prevent is reading the old plan without the new one [user: 「最怕是只讀到舊的 沒有新的」].
5. **A sub-agent reads the docs and hands the main agent the conclusion.** The main agent reads single
   docs itself only when it needs to [user].
6. **It is not all read at once.** All of `docs/` is about 940k tokens by a rough estimate (CJK characters
   counted as one token each, other text as four characters per token; not a real tokenizer). That is
   against a 1M ceiling [user: 「那太大了 我們自己用的就是1m 是上限」]. So the sub-agent reads an index first
   and then the files it picks.
7. **The entry is a skill** [user].
8. **The docs reach the AI as a readonly skill** [user: 「B」]. The skill is copied into the workspace like
   any skill with files (`apps/skills.py:materialize_skill`). Because it is marked readonly:
   - neither the AI nor the user can write into its copy;
   - every `read_skill` compares the copy's `.origin` hashes with what the image ships, and when they
     differ it deletes the copy and copies it again.

   So the copy always matches the deployed docs, and the AI reads real files: whole, with `read_file`,
   or searched with `grep` in the sandbox. Every other skill keeps copy-if-absent (#589: the AI edits
   those copies, and an overwrite would delete its work). Rejected alternatives:
   - **Reading from the image in place** [user: 「我不喜歡新機制」]. It needs a new read path in
     `read_file` / `list_files` / `read_lines`, and the sandbox, which is the only place `grep` runs,
     cannot see the image.
   - **The Platform Help KB collection.** It is too involved, and a searched chunk can miss the
     plan's first line, where the overturned-by id sits.

### Mine, open to override

- **Quota.** A workspace that uses the skill holds about 3.4 MB of docs (the `.md` and `.html` under
  `docs/`: 2.18 MB plans, 1.21 MB the rest). This counts against its quota like any file (the item quota
  is 20 GB by default). Exempting it would be a special case.
- **Where the files come from.** The skill folder holds a symlink to `docs/`, so there is one copy of the
  docs in the repo. `skill_payload` walks with `iterdir` and `is_dir()`, which follow symlinks. The image
  copies `docs/` so the link resolves.
- **The sub-agent.** Sub-agents ship per profile (`.agent/`), and there is no shared set. The skill ships a
  `docs-reader` definition as a file. When `run_agent` does not list `docs-reader`, the agent creates it
  with the existing `save_subagent`, then delegates with `run_agent`. No new sub-agent mechanism.
- **The index.** It has two parts:
  - the mkdocs `nav` (the current docs);
  - `design-history.md` (the plans, one line each).

  `design-history.md` lists 78 of the 147 `plan-` / `handoff-` / `q-` files, so 69 are missing. They are
  added, and a guard test fails when a plan is not in it.
- **Surfaces.** Every shipped app's agent gets the skill (listed in `agent.skills`, as `author-workflow`
  is). KB chat and the /help chat have no skills today, so they are left out of this plan.
- **Workflow agent steps** do not get it. They process data; they do not answer questions about the
  system.

## Phases

- **P1 — readonly skills.**
  - A `readonly: true` field in `SKILL.md`'s front matter. Where it is decided [user, 2026-09-27]:
    - **Upstream decides**: the shipped `SKILL.md` (a shared or profile skill in the package), read at every
      `read_skill`. The copy's own `SKILL.md` never decides, or deleting the field from the copy would
      lift the protection; the copy's `SKILL.md` is overwritten from upstream anyway.
    - **The skill's author sets it**: us, in the repo. `app.json` does not: a skill behaves the same in
      every app.
    - **A skill written in the workspace** (by the user or the AI, no upstream) cannot be readonly: there
      is nothing to compare with or copy from.
    - **Skill hub entries** are out of this plan; only packaged skills can be readonly.
    - **A skill that becomes readonly on an upgrade** overwrites a copy the AI had edited, at its next
      `read_skill`. The runbook entry (P6) says so.
  - For a readonly skill, `read_skill` compares the copy's `.origin` with the upstream hashes
    (`resolve_upstream`). When they differ, it deletes the copy's files and writes upstream's
    (`materialize_skill`'s writes, the manifest last).
  - Writes under a readonly skill's folder are refused at the shared write chokepoint, for the AI's file
    tools and for the user's file API alike.
  - The skills panel shows a readonly skill as readonly.
  - Red-first tests:
    - an older copy is replaced on read;
    - a file upstream removed is removed;
    - an edit is refused;
    - a non-readonly skill is left alone (the control);
    - a copy whose own `SKILL.md` drops `readonly` is still readonly (upstream decides);
    - a workspace skill whose `SKILL.md` says `readonly` is not treated as readonly.
  - Each guard is pinned by a mutation (file copy, restore). Exactly its test must go red when any
    of these is mutated:
    - the line that refuses the write;
    - the line that reads `readonly` from upstream;
    - the hash comparison.
- **P2 — the docs skill.** `sample-skills/system-design/`, registered as a shared skill.
  - `SKILL.md` covers:
    - when to consult the docs: any question about how the system behaves or why, and a user who seems to
      expect something the system does not do;
    - to read the index first;
    - to delegate to `docs-reader`;
    - to follow an overturned-by id to the newer plan;
    - to answer in the user's terms and cite the doc.
  - It ships `docs-reader`'s definition.
  - It is added to every shipped app's `agent.skills` and to `_template`.
- **P3 — the docs in the skill and in the image.**
  - The skill's `docs` symlink, and the Dockerfile copying `docs/`.
  - A parity test: the skill's payload, read through the link, holds exactly the files `docs/` holds.
    The oracle is `docs/` listed at test time, never a hand-kept list.
  - A test that the image build context has it.
- **P4 — the plans put in order.**
  - Every plan gets a line in `design-history.md`.
  - Every overturned plan gets the id of the plan that overturned it on its first lines, in one format:
    `> 被 #NNN（plan-xxx.md）推翻`.
  - The 147 plans are read to find which ones were overturned; each marking names its evidence in the
    commit.
- **P5 — guards.**
  - Every `plan-` / `handoff-` / `q-` file is linked from `design-history.md`.
  - The overturned-by line, where present, has the one format and names a file that exists.
  - A CLAUDE.md rule: a plan that overturns an earlier plan adds that plan's overturned-by line in the
    same PR.
  - Each guard's list comes from `docs/` itself, not from the index it checks. Each guard is proven by a
    mutation: drop a plan's index line, or break a marker's format, and exactly its test goes red.
- **P6 — docs and runbook.**
  - `skills-authoring.md`: `readonly`.
  - A `docs/migrations.md` entry in the same PR, in its four boxes. Every action gives what to do, when
    (rollout 前 / 後), why, and the symptom of skipping it:
    - the image gains `docs/` (the build must include it, or the skill ships a dead link);
    - every app gains a skill, a behaviour change with no knob;
    - a skill made readonly overwrites a copy the AI had edited, at its next `read_skill`.

    確認做完: open an item, ask how some part of the system works, and see the AI read
    `.skill/system-design/` and cite a doc.
- **P7 — live check.** Ask the in-app AI system questions and check it answers from the current design.
  One question is a trap, a decision a later plan overturned: why does boxing 4 tiles on a
  `(group, item)` marking light 4, when `plan-view-plugins-pr5-finish.md` P27 said all combinations
  light? It must answer from `plan-marking-tuples.md`.
- **P8 — review rounds** (four lenses, each in its own worktree), then CI on the final sha.

In every phase:

- The per-change gate: the targeted tests, `ruff check`, `ruff format --check`, `ty check`, and
  `mkdocs build --strict` for docs.
- The three lenses (conformance / veracity / regression) run on my own diff before a push.
- Push, then cancel the CI run the push starts; CI runs only on the final sha, after a clean round.
- Every sentence in docs, comments and commits is written after the check it describes, and every
  count is derived by counting.

## Done means

A user in any shipped app asks how some part of the system works or why. The AI:

- reads the current docs and plans through `docs-reader`;
- never answers from an overturned plan alone;
- says it in the user's terms, citing the doc.

A deploy that changes the docs reaches every workspace's copy on its next `read_skill`.
