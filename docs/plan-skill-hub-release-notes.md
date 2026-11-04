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
- **Update follows `edit_content` alone** (`useItemAccess.canEditContent`),
  not `canWrite`: that is the union of `edit_content` / `add_content` /
  `write_meta`, and the sync route asks `edit_content`, so the union offered a
  button that answers 403. The flag travels WorkspaceShell → ItemChatShell →
  AgentPanel → AgentHeader → SkillsModal (`canUpdateSkills` / `canUpdate`).
- **The mark and the row read one query** (`qk.itemSkills`). Update's existing
  invalidation clears both; a Later / Skip clears the mark through a window
  event (`onRemindersChanged`), since localStorage writes in the same tab fire
  no `storage` event.
- **The old 「skill 已變更」 badge stays** for a behind copy with no `updates`
  (an older pod's answer during a rollout); with `updates` the new row replaces it.
