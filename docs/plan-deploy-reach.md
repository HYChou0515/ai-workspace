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

Overturns `docs/plan-wui-deploy.md`'s panel line — the publisher was told the
RULE ("Anyone who can open this item can use this link.") — and its reader's
404 sentence; the rest of that plan (the URL is a shortcut, not a grant; the
publisher builds, the reader never does) stands.

## Locked decisions (/grill-me, 2026-10-16)

**user** = the user answered it; **mine** = an implementation choice listed for
overturning.

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | Warn, or stop a private Deploy | **Warn, never restrict** (「提醒 不限制」). Deploy always runs; deploying a private page to look at it yourself is a real use. | user |
| 2 | Where the publisher is told | In the **Deploy result panel**, one sentence that states who can open the link NOW, in place of the rule sentence, plus a 「分享設定…」 button that opens the workspace's own share dialog (the one the top bar's access chip opens). The sentence follows a change made in that dialog. | user |
| 3 | The reader's side too | **Yes.** 404: the likeliest cause first — 「打不開這個頁面。最常見的原因是你還沒有這個 workspace 的權限，請向給你連結的人確認他已經分享給你。」 — absence / restoring second, Try again kept (a restoring sandbox answers 404 too). 403 (sees the workspace, not its files): a 「申請存取」 button (`POST …/request-access`, the App dashboard's 🔒 row uses it), which leaks nothing — a 403 is only ever given to someone who may see the workspace. The 404 stays a 404. | user |
| 4 | A publisher who may not change access | Same sentence, **no button**; instead 「要給其他人看，請 workspace 的擁有者 〈名字〉 把他加進來」. Who gets the button: `canChangeItemPermission`, the rule the top bar already uses. | user |
| 5 | The words | private: 「目前只有你打得開這個連結。」 (a publisher who is not the owner — an admin — reads 「目前只有擁有者和管理員打得開這個連結。」); shared: 「只有這個 workspace 的成員打得開這個連結。」, no head-count (a group grant makes the count unknowable); everyone: 「所有人都打得開這個連結。」. **"workspace", never "item"** in anything a person reads — "item" is the developers' word. | user |
| 6 | Only right after Deploy? | **Also on the 「WUI」 overview** (`/wui`): every page card — and the same page's table row — whose link does not open for everyone leads its grey detail line with 「只有你打得開」 for its creator, 「只有擁有者和管理員打得開」 for anyone else (on the overview that can only be an admin, who opens it too — review round 1), 「只有成員打得開」 when shared. An open page carries none. | user (the admin wording was changed in review round 1 without asking, and then put to the user, 2026-10-16) |
| 7 | Translation | The **author toolbar (Refresh … Deploy, Cancel), the Deploy result panel and the reader page's sentences** go through `t()` (zh-TW + en). The rest of the WUI pane keeps its English (another question). | user |
| 7a | Where decision 7 stops | The reader page's OWN sentences (opening, looking again, 404, 403, not a page, signed out, deleted, unreachable, another status), the pane's reader-facing lines (opening, building, not published) and Try again are translated. The low-level read's `reason` (`assets.ts`, e.g. "could not read X (503)") stays English: it also goes to the page's own script (`bridge.ts`) and into the report forwarded to the agent. So that the meaning is still decided in ONE place, `classifyReadFailure` returns a `code` beside `reason`, and the reader page words it by the code. | mine |
| 8 | What "who can open it" is | Computed from the workspace's `permission` the way the server decides a `/w/` read (`read_meta`, then `read_content`, `authorize.py`): absent or `public` → everyone; `restricted` with `all` holding both verbs → everyone; `restricted` with some subject who may pass BOTH verbs → shared — a group's membership is not known to the browser, so a group grant on either verb counts as possibly covering the other verb's grantee (review round 1: `user:bob` + `group:eng` read "owner" while bob opened it); otherwise → the owner. ONE function, `pageReach` (FE), used by the panel and the overview — two copies of this rule would describe one page two ways. | mine |
| 9 | Where the overview gets it | `DeployedPage` gains `item_permission` (the item's `Permission`, or null when it has none) and `item_created_by`: read from the access facts `require_access` has just memoised for the row's gate (`ItemLocator.item_facts`), so they cost no read of their own and are the very creator and permission that gate decided by. 「只有你」 compares the viewer with the CREATOR — `item_owner` is an editable display field (#687) and was the wrong person (review round 1). Not a new exposure: the overview lists only items the viewer may `read_content`, and the item's own GET already returns its permission to anyone who may see it. | mine |
| 10 | 申請存取 for someone already inside | `request-access` notifies nobody when the caller can already enter (`read_chat` — the 「In workspace」 role, which still 403s on the page) and says so with `already_readable: true`. The page reads THAT and says 「你已經在這個 workspace 裡，只是沒有讀檔權限；請直接請擁有者把你的角色調高。」 — never 「已送出申請」 over a request nobody received (review round 1). The route's rule is NOT changed: an earlier, tested decision (`test_request_access_already_readable_for_someone_who_can_enter`) that this PR has no mandate to overturn. `requested: false` WITHOUT `already_readable` is a repeat of a request already with the owner (the route's dedup ledger, shared with the App dashboard's 🔒 row) and reads 「已送出申請」 — round 1 keyed on `requested` and told such a reader they were inside (review round 2). | mine — put to the user (2026-10-16) |
| 11 | Words kept though imprecise | 「目前只有你打得開」 leaves out admins, and 「成員」 includes members granted only Discoverable / In workspace, who cannot open the link. The words are decision 5's, which the user approved; KEEPING them despite these two gaps is this plan's call, not the user's (`ItemMembersPanel`'s "Only you." is the precedent). | mine |
| 12 | The panel's data | The item's own query (`useAppItem`, `qk.appItem`) — the share dialog's mutation already invalidates it, which is what makes decision 2's "follows a change" true without new code. | mine |

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
- GOV.UK Design System, *Page not found pages*
  (design-system.service.gov.uk/patterns/page-not-found-pages): "clear and
  concise, not blame the user", no "technical jargon like 404", no "red text to
  warn people", and a check or a contact route to offer — the reader's 404 is
  muted text naming the person to ask (the sender of the link).
- Material 3 was looked up second, as CLAUDE.md asks: m3.material.io renders
  client-side and its guidance could not be read from here (two fetches came
  back with titles only), so it is not cited; GOV.UK is the next source on the
  list that has a pattern for exactly this page.
- The button is a button (it does something: opens a dialog) — `Btn size="sm"`,
  as Copy beside it.

## Phases

- **P1 — backend**: `DeployedPage.item_permission` (+ `item_created_by`, review
  round 1); `ItemLocator.item_facts` reads the gate's memoised facts. Tests:
  the overview row carries the item's permission and creator; an item with
  none carries null; `item_facts` after the gate reads nothing more
  (`test_item_access_cache.py`).
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

`docs/migrations.md` carries an entry (`#pr-968`): no config key, schema
version, JobType or deploy change, but behaviour changes with no knob — the
WUI toolbar's language, the Deploy panel, the overview's line, the reader's
404/403 — which is CLAUDE.md's "changes behaviour with no knob at all". (The
first push said "no entry"; review round 1 caught it.)
