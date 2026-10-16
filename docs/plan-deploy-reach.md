# Plan — Deploy says who can open the link

## Problem

A Deployed page (`/w/{slug}/{item}/{path}`, `docs/plan-wui-deploy.md`) opens
for exactly the people who can read the workspace's files — the URL is a
shortcut, not a grant (`WuiPage.tsx` module doc). A new workspace is
**private** (`item_routes.py`, `setdefault("permission", {"visibility":
"private"})`), so by default the link opens for its owner (and the admins) and
nobody else. Nothing says so where it matters:

- **The publisher.** After Deploy the panel reads "Anyone who can open this
  item can use this link." — the rule, not the fact. Nothing near Deploy shows
  that this workspace is private, so the link is handed out and does not work.
  The whole strip is hard-coded English.
- **The reader.** Someone without access gets a 404 (the backend does not let
  an outsider probe what exists — `item_authz.check_access`), and the page
  says "There is no file at … or you may not have access … or it is still
  being restored. Try again in a moment." — three causes in one sentence, the
  likeliest one in the middle, and a Try again that never helps for it. A
  reader who can SEE the workspace but not its files gets "You do not have
  permission to read …" and no way forward, though `request-access` exists.

Reported 2026-10-16: 「按下 deploy 時如果是 private，user 會很困惑為什麼別人看不到」.

## Locked decisions (/grill-me, 2026-10-16)

**user** = the user answered it; **mine** = an implementation choice listed for
overturning.

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | Warn, or stop a private Deploy | **Warn, never restrict** (「提醒 不限制」). Deploy always runs; deploying a private page to look at it yourself is a real use. | user |
| 2 | Where the publisher is told | In the **Deploy result panel**, one sentence that states who can open the link NOW, in place of the rule sentence, plus a 「分享設定…」 button that opens the workspace's own share dialog (the one the top bar's access chip opens). The sentence follows a change made in that dialog. | user |
| 3 | The reader's side too | **Yes.** 404: the likeliest cause first — 「打不開這個頁面。最常見的原因是你還沒有這個 workspace 的權限，請向給你連結的人確認他已經分享給你。」 — absence / restoring second, Try again kept (a restoring sandbox answers 404 too). 403 (sees the workspace, not its files): a 「申請存取」 button (`POST …/request-access`, the App dashboard's 🔒 row uses it), which leaks nothing — a 403 is only ever given to someone who may see the workspace. The 404 stays a 404. | user |
| 4 | A publisher who may not change access | Same sentence, **no button**; instead 「要給其他人看，請 workspace 的擁有者 〈名字〉 把他加進來」. Who gets the button: `canChangeItemPermission`, the rule the top bar already uses. | user |
| 5 | The words | private: 「目前只有你打得開這個連結」 (a publisher who is not the owner — an admin — reads 「只有擁有者和管理員打得開」); shared: 「只有這個 workspace 的成員打得開」, no head-count (a group grant makes the count unknowable); everyone: 「所有人都打得開」. **"workspace", never "item"** in anything a person reads — "item" is the developers' word. | user |
| 6 | Only right after Deploy? | **Also on the 「WUI」 overview** (`/wui`): every page card whose link does not open for everyone carries a grey line — 「只有你打得開」 for the owner, 「只有擁有者打得開」 for anyone else, 「只有成員打得開」 when shared. An open page carries none. | user |
| 7 | Translation | The **author toolbar (Refresh … Deploy, Cancel), the Deploy result panel and the reader page's sentences** go through `t()` (zh-TW + en). The rest of the WUI pane keeps its English (another question). | user |
| 8 | What "who can open it" is | Computed from the workspace's `permission` the way the server decides a `/w/` read (`read_meta`, then `read_content`, `authorize.py`): absent or `public` → everyone; `restricted` with `all` holding both verbs → everyone; `restricted` with anyone else holding `read_content` → shared; otherwise → the owner. ONE function, `pageReach` (FE), used by the panel and the overview — two copies of this rule would describe one page two ways. | mine |
| 9 | Where the overview gets it | `DeployedPage` gains `item_permission` (the item's `Permission`, or null when it has none): read from the SAME `find_work_item` the title and owner already come from (`ItemLocator.title_owner_of` → one lookup per item). Not a new exposure: the overview lists only items the viewer may `read_content`, and the item's own GET already returns its permission to anyone who may see it. | mine |
| 10 | The panel's data | The item's own query (`useAppItem`, `qk.appItem`) — the share dialog's mutation already invalidates it, which is what makes decision 2's "follows a change" true without new code. | mine |

UI guidance (CLAUDE.md "UI work must follow published UI/UX guidance"):

- NN/g, *Indicators, Validations, and Notifications*
  (nngroup.com/articles/indicators-validations-notifications): reach is an
  **indicator** — passive, conditional, about one element, so it is "shown in
  close proximity to that element": under the address in the panel, on the
  card on the overview. Not a toast (a passive notification is easy to miss,
  "problematic" when the information is key to understanding the system) and
  not a confirm before Deploy (decision 1).
- NN/g, *Error-Message Guidelines* (nngroup.com/articles/error-message-guidelines):
  the reader's page — describe the specific problem in plain words, **offer a
  way forward** (ask the sender; 申請存取), no blame, no internal codes.
- The button is a button (it does something: opens a dialog) — `Btn size="sm"`,
  as Copy beside it.

## Phases

- **P1 — backend**: `DeployedPage.item_permission`; `title_owner_of` grows to
  the item's facts in one lookup. Tests: the overview row carries the item's
  permission; an item with none carries null; still one lookup per item.
- **P2 — `pageReach`**: the pure function of decision 8, with a case per
  branch (absent, public, private, restricted/all both verbs, all with only
  `read_meta`, user grant, group grant, restricted with only `read_meta`
  grants, unparseable).
- **P3 — the Deploy panel**: reach sentence + 「分享設定…」 (or the owner
  sentence), live after a share change; the author toolbar and the panel
  through `t()`. Tests per decision 2/4/5, the button opening the dialog, the
  sentence changing after the item's permission changes.
- **P4 — the overview**: the grey line per decision 6.
- **P5 — the reader page**: decision 3's 404 / 403 sentences, 申請存取, the
  reader's sentences through `t()`.
- **P6 — docs**: `docs/wui.md` 「發布（Deploy）」 says what the panel and the
  overview now show.

No `docs/migrations.md` entry: no config key, no schema version, no JobType,
no deploy change — `item_permission` is an additive response field and an old
SPA ignores it.
