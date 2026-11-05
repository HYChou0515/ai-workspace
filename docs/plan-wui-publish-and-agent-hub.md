# Plan — publish a WUI, and Agent Hub: each person or conversation gets its own copy

> Status: grilled 2026-10-28/29, then re-grilled 2026-11-04 to split agents out
> of WUI (decisions below, each the user's answer or a recommendation they
> accepted). Build via `/tdd`, flat phases, one commit each.

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
- **There is no agent people can talk to without a workspace of their own.**
  An App's agent is reached only inside an item someone created; a prepared
  "assistant for X" cannot be handed to colleagues.
- **Closest precedent**: skill hub — publish, one version per publish, others get
  a copy (`docs/extending-the-platform.md:660-707`).

## Two things to people, one core underneath

A **WUI** is a screen people operate; an **agent** is an assistant people talk
to. They are published, found and opened in different places, under different
names, with different questions asked at publish time — so nobody mistakes one
for the other. Underneath, both are the same mechanism: a snapshot of a
workspace and copies made from it (C1–C14). The 2026-10-28 grill had merged
them into one "WUI with two entries"; the 2026-11-04 re-grill split them back
apart for the people who use them (W1–W5, A1–A11) and kept the core whole.

## Decisions

### The shared core (both WUIs and agents)

| # | Decision |
|---|---|
| C1 | Published for **other people** to use. |
| C2 | A copy belongs to **the person who opened it**: private, on their quota, with their own sign-ins and env; the publisher does not see it. |
| C3 | A copy holds **the whole workspace** as published — files, settings (preset/model, tool and skill prefs, `env_policy`), dependency folders. **Not** copied: conversations, run journals (`.workflow/`), permission/members, and **schedules** (`schedules.json` anywhere is dropped; there is no per-row pause, and N copies would all fire). |
| C4 | **Shared env values are copied, read from the source workspace when the copy is made** (not frozen at publish), so a refreshed value is the one given. Guidance: tokens belong in each person's Private via sign-in, not in Shared. |
| C5 | Tools: **which** are available and on is copied; **versions** follow the App (`external_tools` floats). |
| C6 | A copy is an **ordinary workspace**; its opener is the owner (they see Shared values too). |
| C7 | Re-publishing affects **only copies made afterwards**; only the latest version is kept. |
| C8 | Only the **user** deletes their copies; no automatic deletion; no per-item cap on how many a person opens — the existing per-person quotas bound it. |
| C9 | Publishing needs **`change_permission`** on the source (the AI can never hold it, `perm/model.py:60-62`). The publisher sets **who may use** it — themselves (default), people/groups (`group:<id>`, #608), or everyone signed in. |
| C10 | The published snapshot counts against the **publisher's disk quota** (one copy: only the latest is kept). The publisher sets the **sandbox size** of copies, within the App's `per_app.max` (#967). |
| C11 | Publishing shows file count, size and the estimated wait to open a copy; above a threshold it names the biggest folders. It warns, never blocks. |
| C12 | Opening a copy shows a **"preparing" page** until it is ready (typing while it prepares is a platform-wide change for later, with ordinary workspaces). |
| C13 | If the source workspace is deleted, what it published is **unpublished** (copies made stay theirs). If the publisher loses access, it stays, managed by whoever now holds `change_permission`. |
| C14 | Anyone the publisher allowed may open it, **from any App**; the copy is made in the source's App. A copy may be published again, marked "based on X". |

### WUI

| # | Decision |
|---|---|
| W1 | The name stays **WUI** (people have learned it); the action is **Publish WUI**, replacing "Deploy". A WUI is a `page.ai.yaml` — a screen. |
| W2 | Publishing **always asks** "how copies are shared" (no default): **a new copy every time** / **one per person** / **one shared by everyone** / **use my workspace directly** (today's behaviour, its cost said beside it: others act inside your workspace and may read each other's private values). An already-Deployed WUI counts as the last one and keeps working unchanged. |
| W3 | "One shared by everyone": the shared copy is the **publisher's**, on their quota; users get a role the publisher picks — Reader / Participant (default) / Collaborator — never change_permission, sharing or terminal. Re-publish makes a new shared copy; the old one stays, as an ordinary workspace in the publisher's name. |
| W4 | Published WUIs are listed on the **`/wui` overview** only — no agents there. Usage numbers are shown to everyone, like skill hub's (no names). |
| W5 | The **WUI skill** (`sample-skills/wui/SKILL.md`) walks the WUI publish choices with `ask_user` (`agent/tools.py:2436`) — how copies are shared, who may use, role (shared copy), sandbox size (copies) — **skipping whatever the user already said**, writes them into the `page.ai.yaml`, then shows a summary and asks the user to **confirm by pressing Publish WUI themselves** (the AI cannot publish, C9). Without `ask_user` it lists the questions in its reply. It covers WUIs only. |

### Agent Hub

| # | Decision |
|---|---|
| A1 | Agents live in their own place, **Agent Hub** (`/agents`), a separate entry in the platform menu beside **WUI**. Always written in full — "Agent Hub", never "hub". The action is **Publish agent** (中文「發布 agent」). |
| A2 | An agent is **one copy per conversation**, fixed — the publisher is not asked how copies are shared (that question is a WUI's, W2). |
| A3 | A published agent has **nothing to do with sub-agents** (`.agent/<name>/AGENT.md`, `run_agent`): different concept, different file. |
| A4 | An agent is defined by an **`agent.ai.yaml`** in **any folder**; a workspace may hold several. It is to an agent what `app.json` (+ the profile) is to an App and may set everything, publishing settings included (they take effect only when someone with C9 publishes). Capabilities can only **narrow within the App** (`⊆`, like a profile, `apps/profiles.py`): no new external tools, presets or tools. |
| A5 | Its **instructions** are the yaml's **`instructions:`** field — one file defines the whole agent, so no `AGENT.md` can be mistaken for a sub-agent's. Its skills and workflows are the workspace root's **plus** its own folder's, its own winning on a name clash. |
| A6 | Opening an `agent.ai.yaml` in the workspace shows an **agent panel**: a summary (name, description, icon, the tools and skills it may use, publish state — not yet / when, who may use, usage), **Publish agent**, and a **test chat** that runs a conversation as this agent in the author's own workspace; the raw yaml stays one switch away. NN/g *Visibility of system status*: the author sees what others will get before publishing. |
| A7 | Agent Hub's **layout is the `/wui` overview's** (`plan-wui-overview.md`, `plan-wui-overview-icon-favourites.md`): cards with an icon (a circle with the title when none is set), a **list mode**, and favourites. No version history, no restore, no update reminder — every new conversation starts from the latest (C7). Usage numbers are shown to everyone, like skill hub's (no names). |
| A8 | An agent's page: description, publisher, last published, usage; **Start a new conversation** on top; below, the viewer's own conversations from it with their size and a bulk delete (C8). **Amended 2026-11-05 (the user, PR #1007):** clicking an agent in Agent Hub goes straight into a conversation — the person's latest, or a first one made on the spot — and this page is reached as "About and history" (from the card, and from History inside a conversation): introduction and history are ONE page. NN/g *interaction cost*, *empty states*, *progressive disclosure*. |
| A9 | Re-publishing takes an optional **release note** ("what changed"); the AI drafts it from what changed and the author confirms or edits it. The agent's page shows the latest few (date, publisher, text). |
| A10 | The **agent skill** (new, beside the WUI skill) teaches writing an `agent.ai.yaml` (`instructions`, the `⊆` narrowing, icon, opening suggestions, publish settings), trying it in the agent panel, and the publish questions — who may use, sandbox size, the release-note draft — **skipping whatever the user already said**, then a summary and the user presses **Publish agent** themselves (C9). |
| A11 | A conversation copy opens **conversation-first**: the agent's icon and name with "Back to Agent Hub" on top, the conversation in the middle, **no "New chat"** (a new conversation is a new copy); the workspace's files, env and tools stay one click away at the side, and `show_file` still raises a file. In the person's item list it is an ordinary item marked "from agent X", and opens the same view. NN/g *Match between system and the real world* (to them it is a conversation) and *Aesthetic and minimalist design*. **Amended 2026-11-05 (the user, PR #1007):** there is ONE bar — the workspace's own top bar, with the copy's title (no icon) — and it carries "Back to Agent Hub", **New conversation** (a new copy) and **History** (the agent's page); still no second chat inside one copy. |
| — | Out of scope: the AI recommending a WUI or an agent to someone (`show_file`). |

## Phases

- **P1 — the published record and its snapshot.** One record for both kinds,
  `Published` (`kind: wui | agent`), replacing `DeployedWui` (an existing row
  reads as a WUI "use my workspace directly", W2) with the C9/C10/W2/W3 choices.
  A publish of the copying kinds archives the whole workspace from the live
  sandbox, minus C3's exclusions, as one stored snapshot on the publisher's
  quota. The route needs `change_permission`.
- **P2 — making a copy.** A new item in the source's App, owned by the opener,
  private (C2/C6), seeded from the snapshot, with the settings of C3, Shared
  values read from the source then (C4), tools per C5, the sandbox size of C10,
  and a "from X" mark. The WUI copying kinds (per open, per person, one shared,
  W2/W3) and the agent's one per conversation (A2); re-publish per C7/W3.
- **P3 — reach and lifecycle.** Who may use (C9), any App (C14), unpublish on
  source deletion and management by `change_permission` holders (C13), "based
  on X" (C14).
- **P4 — WUI screens.** Publish WUI dialog (the W2 question with its costs,
  C9–C11 fields pre-filled from the yaml, C11's counts), the `/wui` overview
  (W4), the "preparing" page (C12). Deploy becomes Publish WUI: labels, i18n,
  `docs/wui.md`; existing rows keep their behaviour (W2). UI guidance: NN/g
  *Visibility of system status* and *Progress indicators* (the wait, C11/C12),
  *Error prevention* (C11's warning, W2's stated cost).
- **P5 — `agent.ai.yaml` and running as an agent.** Its schema (A4, narrowing
  only), `instructions:` (A5), the root + folder merge of skills and workflows
  (A5), one conversation per copy (A2, A11's no "New chat").
- **P6 — the agent panel.** Summary, Publish agent with its dialog (C9–C11,
  the A9 release note), and the test chat (A6).
- **P7 — Agent Hub.** `/agents` in the platform menu (A1), the cards / list /
  favourites layout reused from the `/wui` overview (A7), an agent's page with
  its release notes and the viewer's conversations (A8/A9), and the
  conversation-first copy view (A11). UI guidance: NN/g *Recognition rather
  than recall* and *Consistency and standards* (the same layout as `/wui`,
  a separate place and name — A1/A7).
- **P8 — the skills.** The WUI skill covers WUIs only (W5); the new agent skill
  (A10).
- **P9 — docs.** `docs/migrations.md` (renamed action, new record, the new
  menu entry, nothing to migrate by hand), `docs/wui.md`, a doc for Agent Hub,
  the tokens-not-in-Shared guidance (C4).

## Open items

- A rotating refresh token copied into several copies' Shared would let the
  first refresh invalidate the others (`api/env_refresh.py`); handled
  separately, and mostly moot under C4's guidance.
- Typing while a copy prepares (C12) belongs with ordinary workspaces' cold
  start.
- The test chat (A6) needs a conversation in the author's workspace to run with
  the agent's instructions and narrowed tools instead of the App's; how that is
  marked on the conversation is settled in P6.

## Verification

- Targeted tests per phase, the four gates, mutation-probed applications of
  each rule (exclusions, Shared read at copy time, narrowing only, the C9 gate,
  unpublish on deletion, one conversation per agent copy).
- Live check in a real browser against a base differential: publish each WUI
  kind and an agent, open copies as two people, re-publish (with a release
  note), delete the source; layouts at two widths.
