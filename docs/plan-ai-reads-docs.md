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

- **Quota.** A workspace that uses the skill holds about 3.6 MB: every file the skill carries, which is
  all of `docs/` plus `mkdocs.yml`. This counts against its quota like any file (the item quota
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
- **Workflow agent steps** get it too, as they get every skill their item's app declares (their prompt is
  the item's own, `## Available skills` included). Corrected in review #865 round 1: this line first
  said they do not. Nothing is filtered, since reading a skill costs nothing until a step asks for it.

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
  - The rule goes in CLAUDE.md's `## Workflow`, beside "start with `/grill-me`" and the phase numbering.
    Every Claude session in this repo loads it before any plan is written [user, 2026-09-27]:

    > - **A plan that overturns an earlier plan marks it, in the same PR.** When a new plan reverses a
    >   decision recorded in an older `plan-*.md`, add `> 被 #NNN（plan-new.md）推翻` as the old plan's
    >   first line under its title. The old plan is not rewritten: an AI that reads both can tell which
    >   is current; the one failure is reading the old one alone, and this line is what leads it to the
    >   new one. The format and the target file are guarded (a test); whether a plan overturns another
    >   is not, so the rule is the guard.

  - `design-history.md` opens with one sentence pointing to that rule: a human author edits this file for
    every new plan (the index), so it is where they meet the rule. It is a pointer, not a second copy of
    the rule, since two copies drift.
  - Every other doc under `docs/` is in the mkdocs `nav`, the index of the current docs. Today nothing
    enforces it: `validation.nav.omitted_files` is `info`, and a probe doc left out of `nav` built under
    `--strict` with rc 0 and one INFO line (2026-09-27). So:
    - set it to `warn`, so `--strict` fails such a doc (`plan-` / `handoff-` / `q-` stay in
      `not_in_nav`);
    - add a pytest guard, which runs on every PR.

    A new doc then reaches the AI in three steps:
    - the skill's link carries it into the payload (P3's parity test);
    - the next deploy and `read_skill` refresh the copy (P1);
    - the index names it, or a guard fails.
  - Each guard's list comes from `docs/` itself, not from the index it checks. Each guard is proven by a
    mutation: drop a plan's index line, or break a marker's format, and exactly its test goes red.
- **P6 — docs and runbook.**
  - `skills-authoring.md`: `readonly`.
  - The author's checklist, in `docs/index.md` (the developer guide) [user, 2026-09-27]. For a new doc to
    reach the AI at the next release, its author:
    1. puts it under `docs/` and indexes it: a `plan-*.md` gets a line in `design-history.md`, any other
       doc goes in `mkdocs.yml`'s `nav` (the P5 guards fail the PR otherwise);
    2. when it overturns an older plan, marks that plan (the CLAUDE.md rule above);
    3. merges it as usual.

    Everything else is automatic: the skill's link, the image, and the refresh on `read_skill`.
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

## As built

Where the build differs from the phases above, or found what they did not expect:

- **Only shared skills can be readonly** (P1). The write guard lives in the files facade, which knows a
  path and not an item's app or profile, so it decides by the folder's name against the shared
  registry: a readonly shared skill's name is reserved in every workspace. A profile skill of the same
  name shadows the shared one and is not readonly.
- **The sandbox shell is not guarded** (P1). `exec` changes files in the sandbox without passing the
  facade. A file changed that way stays until the next release changes the skill and the copy is
  replaced whole.
- **The current docs' index is `mkdocs.yml` itself** (P2). The skill links `mkdocs.yml` beside `docs/`.
  `docs/index.md` could not serve: it links 12 of the nav's 45 pages.
- **`.dockerignore` excluded `docs`** (P3). So a `COPY docs/` alone would have carried nothing. It
  is now in the build context, and the P3 tests read `.dockerignore` as Docker does.
- **Six plans sat at the repo root** (P4): `plan-104-remove-source-doc-id`, `plan-blob-gc-job`,
  `plan-chat-column-vertical-space`, `plan-graceful-shutdown`, `plan-show-file-in-chat` and
  `plan-stop-reliability`. They were in no index and outside `docs/`, so they were moved in and
  indexed. A P5 guard now fails any plan at the root.
- **History pages are what `not_in_nav` sets apart** (P5), not only `plan-` / `handoff-` / `q-`:
  `fe-*.md` and `workflows-frontend-brief.md` are history too.
- **P4's result:** 29 plans carry 33 overturned-by lines. The pairs and their evidence are in the P4
  commit.
  - Some decisions were reversed outside any plan file, so they carry no marker: #714's
    workflow env, KB chat queueing, #537's wiki router, #538's quota at the facade, items private by
    default, `read_image` with a vision model, and the workspace tree filter.
- **P7, the live check** (2026-09-27). This machine's ollama runs on CPU and cannot finish a turn, so the
  model was a stand-in that plays a script. Everything else was real: the app, the turn, and every tool,
  which the app executed.
  - Proven:
    - `read_skill("system-design")` returns the body and copies the docs into the workspace (205 files,
      as many as `docs/` holds);
    - the overturned `plan-view-plugins-pr5-finish.md` reads `> 被 #861（plan-marking-tuples.md）推翻`
      under its title, and `plan-marking-tuples.md` reads;
    - a `write_file` into the copy gets the readonly sentence, and the file route gets 403;
    - `save_subagent` saves `docs-reader`, and `run_agent` runs it over both indexes;
    - the skills panel marks the row readonly;
    - a doc added to `docs/` reaches the copy on the next read, and a doc removed leaves it;
    - built for real with the same `COPY` lines and `.dockerignore`, the image resolves the `docs` link
      (205 files). With `docs` excluded again, the build fails on the `COPY`, which is the control.
  - Not proven: that a real model chooses to consult the docs, delegates, or answers well. The
    runbook's 確認做完 asks for exactly that on the deployed model.
  - Found:
    - The `docs-reader` template asked for `read_lines` and `exec`, which playground does not hold, so
      `save_subagent` refused it.
    - The template now asks for `read_file` and `list_files`, which every app and profile holding
      `run_agent` has, and the skill says to add `exec` when held.
- **Review round 1 (#865)** found three (A) findings and fixed them.
  - **A first copy that could not finish stayed half-written for good.** A workspace near its quota, a
    Stop or a rollout mid-copy left files but no `.origin`. The refresh needs the `.origin`, and the
    guard refuses deletes. Fixed in two ways:
    - the copy is checked for room up front, as `install_hub_skill` does, and refused whole;
    - a readonly copy with no `.origin` is cleared and copied again (round 3: only the platform's own,
      told by its `.copying` marker).
  - **The workspace search and replace took in the readonly copy.** Hundreds of docs hits buried the
    person's files. Replace wrote into the copy first and returned 403 for the whole operation, so
    even their own files were left unchanged. Both now skip it, by the facade's same rule
    (`is_readonly`).
  - **The runbook named a symptom that cannot happen.** A dangling `docs` link makes `read_skill`
    fail. It does not produce an empty folder.

  Also fixed:
  - the guard normalises the path, so `/./`, `//` and `x/..` spellings no longer write into the copy;
  - a hub copy that carries a readonly name no longer crashes `read_skill`;
  - several sentences were corrected (the size is 3.6 MB; the sandbox shell is not guarded; mkdocs
    `--strict` runs on master's docs deploy, not on a PR).
- **Known and left** (each (B): rare, or cosmetic):
  - `rmdir /.skill` (the parent) and a move of `/.skill` are not guarded as a whole. A move of the
    folder gets a 403 when it reaches the readonly copy, and a removal is harmless because the next
    read copies the skill again.
  - A profile skill that carries a readonly shared skill's name is refused writes by name, but is not
    refreshed as a readonly copy. No shipped profile has one.
  - The readonly check re-reads the shipped `SKILL.md` on each write into its folder (~30 µs, measured).
    Each `read_skill` hashes the ~3.6 MB payload (5–7 ms, measured).
  - The P4 commit's title says "33 later plans". It is 33 markers naming 22 distinct later plans; the
    body is right.
- **Review round 2 (#865)** found one (A), in round 1's own fix. Clearing a copy with no `.origin`
  would also delete a folder of the person's own that carries the name, written before the name was
  reserved. Round 2 cleared a folder only when every file in it matched a shipped file byte for byte;
  round 3 overturned that (below).
  - Also fixed: a skill applied this turn whose copy does not fit is a "could not load" note, not a turn
    that cannot start. This `WorkspaceFull` was never caught; round 1's whole-copy gate made it happen
    on every turn.
  - Also fixed: the plan's size sentence.
  - Known and left:
    - Two `read_skill` calls at once can briefly leave the first reading a partial copy. The second
      clears and copies again, and both end complete.
    - The search's readonly check reads each skill file's shipped `SKILL.md`, a small read per file.
    - A person's own `.skill/system-design/` is kept but locked by name, and `read_skill` serves it.
      The runbook says how to rename it.
- **Review round 3 (#865)**: all four lenses found the same (A), in round 2's fix. A copy is most often
  cut short by a rollout, and the next read then runs on an image whose docs differ, so the bytes never
  match and the half copy was kept for good. The lenses probed it with a copy the real code wrote and a
  store that fails part-way; one probe also showed a file cut off mid-write fails the byte check the
  same way.
  - The fix: the platform's copy of a readonly skill writes an empty `.copying` marker before its first
    file and removes it after `.origin`. So:
    - no `.origin` but the marker means the platform's copy was cut short, and it is cleared (the marker
      last, so a clearing cut short still reads as ours) and copied again;
    - neither means the folder is the person's own, and it is kept;
    - `.origin` plus the marker means the copy was cut short in the one gap between the two, and the
      marker is dropped.
  - `.copying` is bookkeeping like `.origin`, never one of a skill's files (`skill_payload`,
    `workspace_skill_payload`). Readonly is new in this PR, so no copy exists from before the marker.
  - The same review found the same race twice more, both fixed with a delete that treats "already
    gone" as done:
    - two reads clearing one copy at once: the slower read's delete raised;
    - a refresh cut short after it removed a doc upstream retired keeps the old `.origin`, which still
      lists that doc, so every later refresh raised on it, and the copy never refreshed again.
  - Also fixed: the owner's total across items refuses with `UserDiskFull`, which is not a
    `WorkspaceFull`, and still stopped the turn from the applied-skills block.
  - Every guard line is pinned by mutation: 9 mutations, each reddening its own test.
  - Known and left:
    - A person's own same-named folder with no `SKILL.md` gets the platform's body, which names doc
      paths that are not there.
    - Renaming that folder needs `exec`. An app without it has no way to rename it in the product.

## Done means

A user in any shipped app asks how some part of the system works or why. The AI:

- reads the current docs and plans through `docs-reader`;
- never answers from an overturned plan alone;
- says it in the user's terms, citing the doc.

A deploy that changes the docs reaches every workspace's copy on its next `read_skill`.
