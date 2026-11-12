# Plan — a WUI copy can take the new version

> Status: grilled 2026-11-12 (D1–D6, each the user's answer or a recommendation
> they accepted; D5 as corrected during the grill, see there). Amends C7 of
> `plan-wui-publish-and-agent-hub.md`. Build via `/tdd`, flat phases, one commit
> each.

## Problem

Re-publishing a WUI changes only **copies made afterwards** (C7). Read from
`origin/master` at `8b04d917`:

- **One per person never moves.** The person's copy has a derived id —
  uuid5 of (publication, opener), `api/publish_copy.py:331-336` — and
  `open_copy` hands back the copy they already have before making one
  (`:358-363`). The same link opens the same old copy forever.
- **Nothing says there is a new version.** A copy records which publication it
  came from (`published_from`, `apps/base.py:255`) but not WHICH publish; the
  publication row is updated in place on a re-publish (`Published.published_at`,
  `api/publish.py:87-98`).
- **The only way out is deleting the copy** — and deleting an item is permanent:
  the app's delete route sweeps files, conversations and runs, refunds the quota
  and ends in `permanently_delete` (`api/item_routes.py:930-1118`). There is no
  item trash in the UI.
- **A shared copy splits people.** A re-publish makes a new shared copy and
  leaves the old one (W3); whoever still has the old one open keeps writing into
  it, unaware.
- **WUI publishes carry no "what changed".** The row has `notes`
  (`publish.py:119`) but the dialog offers the note only on an agent's
  re-publish (`web/src/components/PublishDialog.tsx:88`).

## Decisions

| # | Decision |
|---|---|
| D1 | **Taking the new version makes a new copy** from the latest snapshot; the link then opens the new one. The old copy stays — an ordinary item (its `published_from` cleared, so it is no longer anyone's copy and no longer reminds), titled `〈title〉（舊版）`. Not overwritten in place: a copy holds the person's own data, and the platform cannot tell their files from the page's. |
| D2 | **Scope: the three WUI ways that copy.** One per person and one per open: **換新版** (D1). One shared by everyone: **前往新版** — opens the publication, which lands in the current shared copy; no copy is made. Not direct (it IS the source), not an agent's conversations (「新對話」 already starts from the latest). |
| D3 | **Where:** under the reader page's `PageIdentityBar` (`/w/…`, `web/src/pages/WuiPage.tsx`) AND under the workspace's title bar, beside the skill-update banner (`WorkspaceShell.tsx`). One component, #1004's shape — Material 3 *Banners*: a message and text buttons, the dismissing action addresses the message. |
| D4 | **Later / skip, as #1004:** 稍後提醒 = 7 days, fixed; from the second reminder on, 略過這一版; kept in the browser per person + copy + publish (`web/src/lib/skillUpdateReminders.ts` generalised, not copied). |
| D5 | **Not enough room:** checked before anything is made (C8). The banner says how much is needed and how much is free, and offers **刪掉這份並換新版**: confirmed first, with the delete-workspace wording that says it is **permanent** and what goes (files, conversations) — the grill first said "into the trash", corrected once it turned out an item delete is permanent (Problem). Room is judged with the old copy's bytes left out; the new copy is made BEFORE the old one is deleted, by the same cascade the delete route runs. *Recommended; the user's confirmation of the corrected wording is pending.* |
| D6 | **A WUI re-publish takes a release note** too (A9's field and dialog row, offered on any re-publish that copies); the WUI skill drafts it from what changed. The banner shows the first line of the notes since the copy was made; 〔看說明〕 expands them all. |
| — | **How a copy knows it is behind:** a copy records the publish it was made from (`published_version` = the row's `published_at`). A copy made before this field existed is judged by its creation time. A shared copy is behind when it is not the publication's current `shared_copy`. |
| — | **Who is told:** in a per-person / per-open copy, its creator (the person it was made for). In a shared copy, anyone in it who may still use the publication. Nobody, once the publication is taken down or its source is gone. |

## Phases

1. **P1 — the stamp and the question.** `published_version` on `WorkItemBase`,
   set by `make_copy`. `GET /a/{slug}/items/{id}/published-update` answers, for
   the viewer: due or not, the way of sharing, the publish time, the notes since
   the copy, the bytes a new copy needs and whether they fit.
2. **P2 — taking it.** `POST …/published-update` (`replace: false|true`): the
   creator only; room first (C8, leaving the old copy out when `replace`); a new
   copy; the old one renamed `（舊版）` with `published_from` cleared, or —
   `replace` — deleted by the delete route's own cascade, never a second one.
   Answers the new copy's id.
3. **P3 — the banner.** `CopyUpdateBanner` on the reader page and in the
   workspace; reminders shared with #1004's; the not-enough-room state and its
   confirm.
4. **P4 — release notes for WUIs.** The dialog offers the note on a WUI
   re-publish; the WUI skill drafts it.
5. **P5 — docs.** `docs/wui.md` (what a re-publish does to copies, the banner);
   `docs/migrations.md` (existing copies start showing the banner — a change of
   behaviour with no knob).

## Out of scope

- Moving a person's data from the old copy into the new one.
- Updating copies for people (no "update everyone"), or telling them by any
  channel other than the banner.
- Agent conversations (D2).
