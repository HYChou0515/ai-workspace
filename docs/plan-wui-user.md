# Plan — a WUI User can use a page without being able to change the item; env values default to private first

**Status:** grilled 2026-10-21 on master `59811856`; the open questions O1–O3 were
answered the same day (D9–D11).

**Markers:** **[user]** = the user's words or explicit choice · **[verified]** = read from
the code at `59811856` · **[proposal]** = mine, overridable.

## 1. The problem

The user, on two things they want changed **[user]**:

> 我們希望預設改成private first
> 然後要使用wui的人好像幾乎總是需要全部權限 使用者覺得很危險不想開給他們 但不開又用不了

### 1.1 Using someone else's page needs Collaborator [verified]

What a page's buttons hit, and the verb each route requires:

| A page's action | Route | Verb |
|---|---|---|
| Open the page (mint the pass, serve its files) | `POST …/wui/pass`, `GET /wui-content/{token}/…` (`api/wui_content.py:402`, `:429`) | `read_content` |
| Rebuild on open | `POST …/wui/build` (`api/wui_routes.py:352`) | `execute` |
| `startRun` | `POST …/wui/run` (`wui_routes.py:492`) | `execute` |
| `callTool` | `POST …/wui/tools/{name}/call` (`wui_routes.py:670`) | `edit_content` |
| `callAgent`, `startTool`/`startAgent`, job status, stop | `…/wui/agents/{name}/call`, `…/wui/jobs…` (`wui_routes.py:857`, `:883`, `:897`, `:904`) | `execute` |
| `writeFile` / `deleteFile` | the workspace's own `PUT`/`DELETE …/files/{path}` | `edit_content` |

- The role ladder (`web/src/lib/itemPermission.ts:40-44`) is nested: Discoverable ⊂ In
  workspace ⊂ Reader ⊂ Participant ⊂ Collaborator. Nothing between Participant and
  Collaborator holds `execute` or `edit_content`, and Collaborator is "Can also edit files
  + run code" on the whole item. So a page with a single working button needs a role
  that can also edit every file, open the item's agent on anything and deploy.
- A page's writes do not carry the page's pass. `writeFile` goes through
  `postMessage` to the platform's own pane (`web/src/renderers/wui/bridge.ts:286-296`),
  which calls the ordinary file service with the viewer's session. The server cannot
  tell a page's save from a save in the file tree.
- "A page can only write inside its own folder" is checked only in the browser
  (`resolveWritePath`, `cannotWrite`, `bridge.ts:135-139`, `:292`).
- What a page runs is authorised as the item's OWNER (`captured_user`), with the
  pressing person's private env values (`wui_routes.py:525-534`, plan-wui-viewer-login
  Q6). That does not change here.

### 1.2 The env default [verified]

- Each item stores a policy per variable name, `env_policy` (`apps/base.py:110`); a name
  with no entry is `shared_first` (`api/env_layers.py:104`, FE `web/src/lib/envLayers.ts:15-18`).
- Orders (`env_layers.py:106-111`): `shared_first` = shared → private → personal → service;
  `private_first` = private → personal → service → shared; `private_only` = private →
  personal → service.
- The Shared choice is stored as ABSENCE (`web/src/components/EnvVarsModal.tsx:165-172`),
  so "never set" and "deliberately chose Shared" cannot be told apart.
- `plan-wui-viewer-login.md` §2 (line 58) recorded the default as `shared_first` so that
  "升級不改變任何既有行為〔user〕". This plan overturns that.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | What must be blocked is action OUTSIDE the page — editing or deleting files in the file tree, the terminal, changing the page, deploying. Doing what the page is built to do, through the page, is allowed. This prevents mistakes; it is not a defence against someone crafting requests by hand **[user]** 「A」. |
| D2 | The permission model gains a new verb, "use WUI" (`use_wui`) **[user]** 「要多一個權限：使用wui」. |
| D3 | A holder of `use_wui` can do everything the page can do, and cannot leave the page to change the item: no file-tree edits or deletes, no terminal, no changing the page, no deploy **[user]** 「對」. |
| D4 | A WUI User can enter their own values (their own sign-in, their own env values); they cannot see or change the item's shared values **[user]** 「要」. |
| D5 | The env default becomes `private_first`, for existing items too. From now on choosing Shared in the panel is stored explicitly and is not affected by the default **[user]** 「a」. |
| D6 | A WUI User who opens the item sees the whole workspace read-only, exactly as a Reader does — no separate screen **[user]** 「B這樣不用做特規」. |
| D7 | Someone without `use_wui` who presses a page's button sees, above the page, "this page needs the WUI User permission" and "申請存取" (#968's request flow); someone who cannot change permissions sees the owner's name instead (#968 D4) **[user]** 「Ok」. |
| D8 | The new role is called **WUI User** **[user]** 「WUI User」. |
| D9 | Whoever may talk to the item's agent (`converse`, Participant) may also use pages: `converse` implies `use_wui`, so existing Participants keep their role name and gain page use (O1) **[user]** 「可以」. |
| D10 | Whoever may run code (`execute`, every Collaborator) may also use pages: `execute` implies `use_wui`, so existing Collaborators do not lose page use. A Custom grant of `execute` without `edit_content` can now also save through a page, inside the page's folder (O2) **[user]** 「對」. |
| D11 | `private_first` keeps its order (private → personal → service → shared). A run nobody presses — a schedule — therefore uses the deployment's service-account value over the item's shared value for a name both hold, where today it uses the item's. The runbook says so (O3) **[user]** 「B」. |
| D12 | A page's requests carry a header naming the page (its view file's path). A route admits its own verb as today, or — through a page — `use_wui` with four checks: the view file exists, the folder is derived from it on the server, the path is inside that folder (not at the root), and the path is not the view file. Writes keep the workspace's own routes **[user]** 「或是wui放header 後段檢查細一些 這樣不至於全部人都能透過加header亂搞」, 「這樣應該是改動最少的方式」. |

## 3. Mechanism

### 3.1 The verb (D2)

- `perm/model.py`: `"use_wui"` joins `Verb`, `VERBS` and `Permission` (a grant list, like
  every other verb) **[proposal]**. Not indexed — only `read_meta` / `read_content` are
  (`resources/__init__.py:495-496`), so no backfill.
- `perm/authorize.py` `_effective_grants`: `use_wui` also admits whoever holds `converse`
  (D9) or `execute` (D10), and `read_content` admits whoever holds `use_wui` — a page that
  cannot read its own files cannot open (D3).
- `AI_FORBIDDEN` does not gain it: the item's agent never acts through a page.

### 3.2 The page's routes (D3, D12)

A route admits a request when the person holds the route's own verb, as today, **or** the
request comes through a page and the person holds `use_wui` — a union, so nobody loses
what they can do now.

- **The WUI routes** (`…/wui/build`, `…/wui/run`, `…/wui/tools/{name}/call`,
  `…/wui/agents/{name}/call`, `…/wui/jobs` and its status / stop) are only ever called by a
  page, so each takes its current verb or `use_wui`.
- **File writes and deletes stay on the workspace's own routes** (`PUT` / `DELETE
  …/files/{path}`) — no new route (D12, 「這樣應該是改動最少的方式」). The pane's bridge adds a
  header naming the page: the path of its view file. Without `edit_content`, the server
  admits the write only when all four hold:
  1. the person holds `use_wui`;
  2. a view file exists at the header's path — the page's folder is derived from that file
     on the server, never taken from what the browser sends (a pass can be minted for any
     folder, `api/wui_content.py:401-405`, so its folder is not evidence of a page);
  3. the written path is inside that folder (`_inside`, `wui_content.py:299-303`, compared
     on segments); a page at the workspace root has no folder and is refused, as the
     browser already refuses it (`bridge.ts:135-139`);
  4. the written path is not the view file itself — it declares the workflows, tools and
     agents the page may call, so writing it would widen what the page may do.
- **What the header buys.** The file tree, uploads, renames and every other workspace
  surface send no header, so a WUI User's write from any of them is refused by the server —
  a surface someone forgets to gate fails closed. Someone crafting the header by hand can
  at most change files in a page folder they may use, never the view file and never
  anything outside it (D1 accepts crafted requests; D12 bounds them).
- Deploy / undeploy (`wui_deploy.py`) stay on `edit_content`; the terminal on
  `use_terminal`.

### 3.3 The role (D6, D8)

- A preset **WUI User** = Reader + `use_wui`, placed after Reader on the ladder. Participant
  and Collaborator include it by the ladder, and existing ones are read as such through
  the implications (D9, D10).
- The permission dialog's Custom mode offers "使用 WUI" as its own checkbox
  (`ItemVerb`, `itemPermission.ts:17-24`).
- The workspace already shows a Reader a read-only workspace; a WUI User is shown the same
  (D6). The pane draws the page's buttons working for them.

### 3.4 Own values (D4)

- The Env button is shown to `converse` or `write_meta` holders today
  (`WorkspaceShell.tsx:1092`, `canOpenEnv={_canConverse || _canWriteMeta}`); `use_wui`
  joins them. The panel's Shared tab stays read-only without `write_meta`, as it is.
- The routes for a person's own values already need only `read_meta`
  (`api/private_env.py`), so nothing changes on the server.

### 3.5 The default (D5)

- `env_layers.layer_of`: a name with no entry reads as `private_first`; FE `policyOf`
  likewise. The parity test (`web/tests/envLayersParity.test.ts`) and its fixture are
  regenerated, not hand-edited.
- `EnvVarsModal.choosePolicy`: choosing Shared stores `"shared_first"` explicitly; only
  the new default (`private_first`) is stored as absence.
- Existing items: no migration. Every name with no entry — never set, or chosen as Shared
  before this — now reads as `private_first` (D5 「a」). The runbook says how to keep a
  name on the old behaviour.

### 3.6 The refusal (D7)

- A page-route 403 for lack of `use_wui` carries a typed reason; the pane draws a banner
  outside the frame with "需要 WUI User 權限" and #968's request-access button (or the
  owner's name for someone who cannot change permissions). The page's own code still gets
  its error.

## 4. Phases

Flat integers; each built with `/tdd` and committed on its own.

| Phase | Content |
|---|---|
| P1 | `use_wui` in the verb model; `_effective_grants` implications; authorize tests |
| P2 | WUI routes gated on `use_wui` (build, run, tools, agents, jobs) |
| P3 | The page header on the bridge's writes; `…/files` admits a `use_wui` write under D12's four checks |
| P4 | WUI User preset + Custom checkbox; the ladder (D9, D10); Env button for `use_wui` (D4) |
| P5 | The refusal banner with request access (D7) |
| P6 | Default `private_first`; Shared stored explicitly; parity fixture regenerated (D5) |
| P7 | Docs: overturn line on `plan-wui-viewer-login.md`; `docs/wui.md`, `docs/extending-the-platform.md` (also its stale "shared_first does not read 我的環境變數", wrong since #917), permission docs; `docs/migrations.md` entry (behaviour changes with no knob — including D11: a schedule now prefers the service account over the item's shared value for a name both hold) |

## 5. Questions answered after the grill

O1–O3 were open when this plan was first written; the user answered them the same day.

- **O1 → D9.** The ladder is nested, so WUI User between Reader and Participant puts
  `use_wui` in Participant. Existing Participants' stored grants do not hold it; reading
  `converse` as implying `use_wui` keeps them Participant, with page use.
- **O2 → D10.** Existing Collaborators' grants hold `execute` / `edit_content`, not
  `use_wui`; reading `execute` as implying it keeps their page use.
- **O3 → D11.** The alternative was to move the service account last
  (private → personal → shared → service) so a schedule keeps today's value; the user chose
  to keep `private_first`'s order.
