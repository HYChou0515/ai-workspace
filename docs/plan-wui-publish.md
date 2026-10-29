# Plan — publish a WUI: each person or conversation gets its own copy

> Status: grilled 2026-10-28/29 (decisions below, each the user's answer or a
> recommendation they accepted). Build via `/tdd`, flat phases, one commit each.

## Problem

People want to hand a workspace they prepared — files, skills, sub-agents,
workflows, tools, settings — to others, so that **every new conversation (or
every person) starts from a fresh sandbox holding what it held when it was
published**. Today nothing does that. Read from `origin/master` at `a32bc7d0`:

- **WUI "Deploy" copies nothing.** It records one `DeployedWui` row
  (`api/wui_deploy.py:50-107`); the link always opens the folder's current
  output, in the author's own item (`docs/wui.md:217-248`). No snapshot was a
  locked decision (`docs/plan-wui-deploy.md:29`).
- **One live sandbox per item, shared by all its chats** (`api/registry.py:436-444`,
  `CLAUDE.md` "Workspace files are SHARED-DIR"). Everyone using a deployed page
  works in the author's workspace, and anyone there who can run code can read
  another person's private values (`docs/plan-wui-viewer-login.md:100-106`) —
  the complaint about WUI this plan answers.
- **No item can be copied.** The only seed source is a git-shipped App profile
  (`apps/seeding.py:29-73`, `api/item_routes.py:302-387`).
- **Closest precedent**: skill hub — publish, one version per publish, others get
  a copy (`docs/extending-the-platform.md:660-707`).

## Decisions

| # | Decision |
|---|---|
| D1 | Published for **other people** to use. |
| D2 | A copy belongs to **the person who opened it**: private, on their quota, with their own sign-ins and env; the publisher does not see it. |
| D3 | The name stays **WUI** (people have learned it); the action is **Publish**, replacing "Deploy". A WUI has an **entry**: `page.ai.yaml` → a **screen**, `agent.ai.yaml` → a **conversation**. The overview marks the two with icons; "page WUI" is not a term. |
| D4 | Publishing **always asks** "how copies are shared" (no default): **a new copy every time** (a conversation entry: one per conversation) / **one per person** / **one shared by everyone** / **use my workspace directly** (today's behaviour, its cost said beside it: others act inside your workspace and may read each other's private values). An already-Deployed WUI counts as the last one and keeps working unchanged. |
| D5 | A copy holds **the whole workspace** as published — files, settings (preset/model, tool and skill prefs, `env_policy`), dependency folders. **Not** copied: conversations, run journals (`.workflow/`), permission/members, and **schedules** (`schedules.json` anywhere is dropped; there is no per-row pause, and N copies would all fire). |
| D6 | **Shared env values are copied, read from the source workspace when the copy is made** (not frozen at publish), so a refreshed value is the one given. Guidance: tokens belong in each person's Private via sign-in, not in Shared. |
| D7 | Tools: **which** are available and on is copied; **versions** follow the App (`external_tools` floats). |
| D8 | A copy is an **ordinary workspace**; its opener is the owner (they see Shared values too). |
| D9 | Re-publishing affects **only copies made afterwards**; only the latest version is kept. "One shared by everyone": re-publish makes a new shared copy; the old one stays, as an ordinary workspace in the publisher's name. |
| D10 | Only the **user** deletes their copies; no automatic deletion; no per-WUI cap on how many a person opens — the existing per-person quotas bound it. |
| D11 | Publishing needs **`change_permission`** on the source (the AI can never hold it, `perm/model.py:60-62`). The publisher sets **who may use** it — themselves (default), people/groups (`group:<id>`, #608), or everyone signed in. |
| D12 | "One shared by everyone": the shared copy is the **publisher's**, on their quota; users get a role the publisher picks — Reader / Participant (default) / Collaborator — never change_permission, sharing or terminal. |
| D13 | The published snapshot counts against the **publisher's disk quota** (one copy: only the latest is kept). The publisher sets the **sandbox size** of copies, within the App's `per_app.max` (#967). |
| D14 | Publishing shows file count, size and the estimated wait to open a copy; above a threshold it names the biggest folders. It warns, never blocks. |
| D15 | Opening a copy shows a **"preparing" page** until it is ready (typing while it prepares is a platform-wide change for later, with ordinary workspaces). |
| D16 | `agent.ai.yaml` may sit in **any folder**; a workspace may hold several. It is to an agent what `app.json` (+ the profile) is to an App and may set everything, publishing settings included (they take effect only when someone with D11 publishes). Capabilities can only **narrow within the App** (`⊆`, like a profile, `apps/profiles.py`): no new external tools, presets or tools. |
| D17 | An agent's instructions are its folder's `AGENT.md`; its skills, sub-agents and workflows are the workspace root's **plus** its own folder's, its own winning on a name clash. |
| D18 | A conversation copy holds **one conversation**: no "New chat" inside it — a new conversation is a new copy. |
| D19 | All published WUIs are listed on the **`/wui` overview** (screen / conversation icons). Usage numbers are shown to everyone, like skill hub's (no names). An agent's page: "Start a new conversation", then the viewer's own conversations from it with their size and a bulk delete. |
| D20 | If the source workspace is deleted, its WUIs are **unpublished** (copies made stay theirs). If the publisher loses access, they stay, managed by whoever now holds `change_permission`. |
| D21 | Anyone the publisher allowed may open it, **from any App**; the copy is made in the source's App. A copy may be published again, marked "based on X". |
| D22 | The **WUI skill** (`sample-skills/wui/SKILL.md`) walks the publish choices with `ask_user` (`agent/tools.py:2436`) — entry, how copies are shared, who may use, role (shared copy), sandbox size (copies) — **skipping whatever the user already said**, writes them into the `*.ai.yaml`, then shows a summary and asks the user to **confirm by pressing Publish themselves** (the AI cannot publish, D11). Without `ask_user` it lists the questions in its reply. |
| — | Out of scope: the AI recommending a WUI to someone (`show_file`). |

## Phases

- **P1 — the published record and its snapshot.** `PublishedWui` replaces
  `DeployedWui` (an existing row reads as "use my workspace directly", D4) with
  the D4/D11/D12/D13 choices. A publish of the copying kinds archives the whole
  workspace from the live sandbox, minus D5's exclusions, as one stored snapshot
  on the publisher's quota. The route needs `change_permission`.
- **P2 — making a copy.** A new item in the source's App, owned by the opener,
  private (D2/D8), seeded from the snapshot, with the settings of D5, Shared
  values read from the source then (D6), tools per D7, the sandbox size of D13,
  and a "from X" mark. The three copying kinds (per open, per person, one
  shared, D4/D12); re-publish per D9.
- **P3 — `agent.ai.yaml`.** Its schema (D16, narrowing only), `AGENT.md` and the
  root + folder merge (D17), one conversation per copy (D18).
- **P4 — reach and lifecycle.** Who may use (D11), any App (D21), unpublish on
  source deletion and management by `change_permission` holders (D20), "based
  on X" (D21).
- **P5 — the screens.** Publish dialog (the D4 question with its costs, D11-D14
  fields pre-filled from the yaml, D14's counts), the `/wui` overview (D19), an
  agent's page, the "preparing" page (D15). UI guidance: NN/g *Visibility of
  system status* and *Progress indicators* (the wait, D14/D15), *Recognition
  rather than recall* and *Consistency and standards* (one overview, one publish
  flow), *Error prevention* (D14's warning, D4's stated cost).
- **P6 — Deploy becomes Publish.** Labels, i18n, `docs/wui.md`; existing rows
  keep their behaviour (D4).
- **P7 — the WUI skill** (D22).
- **P8 — docs.** `docs/migrations.md` (renamed action, new record, nothing to
  migrate by hand), `docs/wui.md`, the tokens-not-in-Shared guidance (D6).

## Open items

- A rotating refresh token copied into several copies' Shared would let the
  first refresh invalidate the others (`api/env_refresh.py`); handled
  separately, and mostly moot under D6's guidance.
- Typing while a copy prepares (D15) belongs with ordinary workspaces' cold
  start.

## Verification

- Targeted tests per phase, the four gates, mutation-probed applications of
  each rule (exclusions, Shared read at copy time, narrowing only, the D11
  gate, unpublish on deletion).
- Live check in a real browser against a base differential: publish each kind,
  open copies as two people, re-publish, delete the source; layouts at two
  widths.
