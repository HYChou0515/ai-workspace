# Plan — skill hub release notes and update reminders

> Status: grilled 2026-11-04 (D1–D6, each the user's answer or a recommendation
> they accepted). Build via `/tdd`, flat phases, one commit each.

## Problem

When an author publishes a new version of a skill, the people who installed it
should **know there is an update, read what changed, and choose whether to
take it**. Read from `origin/master` at `091da788`:

- **Versions exist, notes do not.** Each publish is a git commit in the entry's
  bare repo with a hard-coded message, `f"publish {name}"`
  (`apps/skill_hub.py:1122`, `:1188`); `publish_skill` takes only a name
  (`agent/tools.py:2768`). The history (`HistoryEvent`, `skill_hub.py:425`)
  shows vN, date, description and review notes — nothing the author wrote about
  the change.
- **"Behind" is known only on request.** `skill_upstream` (`apps/skills.py:837`)
  compares the copy's `.origin.commit` with the entry's (G13), computed only when
  an item's skill list is fetched (`api/file_routes.py:370-424`). Nothing tells
  anyone who has not opened that list; the skills button in the workspace
  (`web/src/pages/investigation/AgentPanel.tsx:1812-1819`) carries no mark.
- **Syncing exists.** `POST …/skills/{name}/refresh` is a per-file three-way merge
  (`skills.py:1013-1110`): files the user edited are kept and listed.
- **No notification would reach anyone**: the in-app bell is a hard-coded
  placeholder (`WorkspaceShell.tsx:1555-1576`).

## Decisions

| # | Decision |
|---|---|
| D1 | The reminder lives **inside the item**: the skill list says "有新版本 vN", and the workspace's skills button carries a mark (how many skills have one). No platform-wide bell. |
| D2 | The release note is **drafted by the AI from the change and confirmed or edited by the author** before publishing. Two halves: `publish_skill` requires a `release_note` from v2 on — without one it publishes nothing and returns what changed since the current version, for the AI to draft from; the skill-hub `SKILL.md` (what the AI reads when publishing) says how to write it (what was added, changed, what to watch for) and to confirm it with the author through `ask_user` before publishing. The tool guarantees a note; the skill makes it a good one. v1 needs none. |
| D3 | The first reminder offers **Update / Remind me later**; every later one adds **Skip this version**. "Later" is a fixed **7 days**; the user cannot change it. |
| D4 | Later and Skip are kept in **localStorage**, keyed by user, entry and version, and judged **only once the current user is known** (`useCurrentUserState().ready` — #997: judging on the "default-user" placeholder showed a dismissed reminder on every cold load). **Update** shows only to someone who may edit the item's content (`edit_content`), since it changes the copy for everyone in it. |
| D5 | The reminder shows the note of **every version after the one installed**, newest first (the newest expanded). A version published before this feature reads "這一版沒有說明" with a link to compare it (the existing diff). Update installs **only the latest** (as G23). If the author rolled master back below the installed version, it reads "作者已把這個 skill 回到 vN" with Update. |
| D6 | **Merging is out of scope.** Update is the existing sync, unchanged. |
| D7 | (2026-11-04, after the first build; extends D1) **A banner on the item, above the composer**, seen on opening it — the user: 「他要打開面板才看得到 -> 不會有人看到」. At an ordinary width the header folds into "⋯", so the mark alone was a dot three clicks from the notes. One banner however many skills are due; two text buttons, 看更新說明 (opens the notes in place, with Update / Later / Skip) and 稍後提醒 (puts off every skill it names); no lone ×. Shown to everyone who opens the item; Update still only for `edit_content`. Material 3 Banners; NN/g *Indicators, Validations, and Notifications*. The skill-row text and the button mark stay. |

**Where the note lives:** the version's **git commit message** — each publish is
one commit already, so there is no new field and no `Schema` bump. History and
reminder read it from the same place.

## Phases

- **P1 — the note on the version.** `publish(…, release_note)` writes it as the
  commit message; `HistoryEvent` carries it (read from the commit); the history
  view shows it per version.
- **P2 — the tool requires it.** `publish_skill` from v2 on refuses without a
  `release_note` and answers with the change since the current version (the
  existing diff, capped). Tests: refusal + the change it returns; a note given is
  stored.
- **P3 — the skill teaches it.** `sample-skills/skill-hub/SKILL.md`: draft from
  the change, confirm with `ask_user`, then publish with the note; scenarios for
  `skill_eval` (draft asked for, not invented; confirmed before publish).
- **P4 — what changed since mine.** The item's skill list returns, per behind
  copy, the versions after the installed one with their notes (D5), and the
  rolled-back case.
- **P5 — the reminder.** Skill list badge "有新版本 vN" (text, not an icon);
  workspace skills-button mark with a count; the reminder panel with the notes
  (D5) and the buttons of D3, Update only for `edit_content` (D4); Later / Skip
  in localStorage judged only when the user is ready (D4). UI guidance: NN/g
  *Visibility of system status* (a mark where the skills are), *User control
  and freedom* (Later / Skip), *Recognition rather than recall* (the notes beside
  the button).
- **P6 — docs.** `docs/extending-the-platform.md` skill hub section;
  `docs/migrations.md` entry (publishing a new version now asks for a note; the
  tool's contract changed).

## Verification

- Targeted tests per phase, the four gates, the FE typecheck; mutation-probed
  applications of each rule (the v2+ refusal, the note in the commit, the
  versions-after list, the `ready` gate, the `edit_content` gate, the 7-day and
  skip keys).
- A live check in a real browser (stand-in model for the drafting turn) against
  a base differential: publish v1 → install → publish v2 with a drafted note →
  the item shows the mark and the note; Later, then after the 7 days (clock
  injected) Skip; Update; two widths.

## As built (#1004)

- **The note is the commit message**, `publish <name>`, a blank line, then the
  note (`skill_hub_git.publish_message`); history reads it back with one
  `git show --no-walk` over the listed commits (`SkillHubRepos.release_notes`).
- **The refusal sits after the size and structure checks** (a folder that
  cannot load gets those errors first, so there is nothing to describe yet)
  and **before the workspace room check and the review**, so no reviewer is
  spent on a version that is not published. Its words are one function,
  `release_note_needed`, shared with the `skill_eval` double.
- **P3's scenario needed the double to change.** The `publish_skill` double
  published every call, so the reply the guidance teaches from never
  reached the model. A scenario now stages `published-skills.txt` (names
  already on the hub; not `.json`, which `load_scenarios` would read as a
  scenario). For those names the double refuses without a note, with a change
  built by the real `_changes_since`. Scenario: `republish-drafts-a-note`.
  What it measures is narrower than P3 asked: the scorer checks which tools
  were called, not in what order, so it shows the model reached for
  `publish_skill` AND `ask_user`, not that it asked before publishing; and
  the refusal text itself says to confirm with the user, so a `--control`
  run may pass it too — `--control` is what tells.
- **The change to draft from is its own diff, not the hub's.** P2 said "the
  existing diff", but `SkillHubStore.diff` compares two committed revisions,
  and the version being refused is not committed. `_changes_since` diffs the
  workspace payload against the current version's files with difflib,
  following the hub's rule for what is too large to diff (`DIFF_TEXT_CAP`,
  shared, not copied), in a worker thread.
- **`release_note` is nullable.** `publish_skill`, like nearly every built-in, is
  a strict tool, which makes each argument a required slot; a `str` note had no "not setting it"
  spelling, so a model invented one on every call — a republish's included,
  which skips the ask. `str | None` gets the platform's absent convention
  (`null`), and the skill says to call with `null` first. A note is capped at
  `RELEASE_NOTE_MAX` (2000 characters): it is read back in every history view
  and every behind copy's listing.
- **Update follows `edit_content` alone** (`useItemAccess.canEditContent`),
  not `canWrite`: that is the union of `edit_content` / `add_content` /
  `write_meta`, and the sync route asks `edit_content`, so the union offered a
  button that answers 403. The flag travels WorkspaceShell → ItemChatShell →
  AgentPanel → AgentHeader → SkillsModal (`canUpdateSkills` / `canUpdate`).
- **The mark and the row read one query** (`qk.itemSkills`). Update's existing
  invalidation clears both; a Later / Skip clears the mark through a window
  event (`onRemindersChanged`), since localStorage writes in the same tab fire
  no `storage` event — and the `storage` event carries another tab's. A
  browser that refuses the write keeps the answers for the page.
- **The mark is a count.** D1's "how many" is drawn as a number badge on the
  skills button (Material 3 Badges), not only said in the tooltip; folded
  into "⋯", the trigger carries a dot and its name lists what is flagged.
- **The mark costs a skill-list read per item open** (cached 30 s), where the
  list used to be read only when the panel opened. Each behind hub copy reads
  its timeline once (`updates_since` returns each version's `back_to` from
  the same read), and a copy whose timeline cannot be read loses only its own
  `updates`.
- **History numbers versions in the order they were written.** Recording the
  demo on the disk backend showed the newest publish as "v1" and the first as
  "v2 · 目前版本", with the note under the wrong number: `history()` counted
  along `list_revisions()` as if it were oldest first, which only the
  in-memory backend promises. It now sorts by the revision's time. The bug
  predates this plan; it matters here because `updates_since` and the
  reminder read the same order. Pinned on both backends
  (`tests/apps/test_skill_hub_history_order.py`).
- **A mark on a folded header action shows on the "⋯" button.** At 1280px
  the chat header folds its actions into "⋯", and the dot was drawn only on
  unfolded buttons, so the skills mark (and the older env-missing one) was
  invisible. The trigger now carries it and names what it hides.
- **The old 「skill 已變更」 badge stays** for a behind copy with no `updates`:
  a copy installed before the git store (it records no commit, so there is
  nothing to count from — for good), one whose timeline could not be read, or
  an older pod's answer during a rollout. With `updates` the new row replaces it.
