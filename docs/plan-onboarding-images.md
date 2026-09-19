# Plan — onboarding 內文走 markdown、可放圖（內文或最下面）

## 背景

#161 的 onboarding（`<OnboardingModal>`，Launcher 的平台層 + AppDashboard 的 per-App 層）內容結構是
`{version, title, intro, points[{title, body}]}`，`intro` / `body` 目前被當**純文字**塞進 `<p>`
（`web/src/components/OnboardingModal.tsx:45,75`）。想在 modal 裡放截圖——在某個 point 的內文裡、或整段
最下面——現在做不到，`![](x.png)` 會原樣顯示成字。

grill（2026-09-19）逐題定案如下；每一條都有對應的程式碼事實，不是偏好。

## 程式碼事實（決定形狀的）

- **共用的 markdown 渲染器**是 `web/src/renderers/MarkdownRenderer.tsx:MarkdownBody`（GFM、math、圖與連結
  的相對路徑解析）；它的 docstring 明講「不要在旁邊長第二條較差的 markdown 管線」。它內部呼叫
  `useFileService()`，**沒有 provider 會 throw**；而 modal 掛在 Launcher / AppDashboard，兩處都在
  `FileServiceProvider` 之外。
- `.md-body` 樣式是**全域**的（`web/src/styles/base.css:233`），已有 `md-compact` 變體——不是被 scope 住的
  class，沿用不會零樣式。**但它自帶 `color: var(--text-paper)` 與 `font-size`**（compact 是 13px）：modal 的
  intro 是 14px、內文是 13px、顏色是 `--text-paper-d`（暗一階），寫在外層 wrapper 的 inline style 上；元素自己的
  class 規則會蓋掉從父層繼承的值，所以 wrapper 那三個屬性會**死掉**（round 1 四把鏡頭各自量到：article
  `13px / #1A1B1F`，wrapper `14px / #5C5F66`）。修法照 `kb.css:151` 的 `.kb-msg__text.md-body`：一個情境
  class 讓 article `inherit` 回 wrapper 的值（見下方 as-built）。
- **App 自帶檔案給瀏覽器的先例**：`GET /apps/{slug}/icon`（`api/meta_routes.py:154`）→
  `apps/manifest.py:load_app_icon`：純檔名、任何分隔符一律拒絕（不可能跳出 App 目錄）、副檔名白名單
  `ICON_MEDIA_TYPES`（png / svg / jpg / jpeg / webp / gif）、任何不成立都是 404。`/apps/{slug}` 與 `/icon`
  沒有額外授權（任何登入者可讀）。
- 平台層 welcome 是 FE 常數 `web/src/lib/platformOnboarding.ts`，由同一個 modal 元件畫；它沒有 App 目錄。
- `ModalShell` 本身會捲（`maxHeight: 85vh` + `overflowY: auto`），onboarding 的 modal 寬 460px。
- 五個內建 App 的 onboarding 文字掃過一次（`_template` / `playground` / `pm` / `rca` / `topic-hub`）：只有 `pm`
  有一處反引號（`` `issues/N.md` ``），改成 markdown 後變行內程式碼，正是作者原意；沒有 `*` `_` `#` `[` 會被誤判。
  平台常數也乾淨。
- `docs/adding-an-app.md` 的 `app.json` 欄位表**沒有列 `onboarding`**。

## 已拍板的決定

1. **結構保留，欄位改語意**：`intro` 與 `points[].body` 改成 markdown；新增選填 `footer`（markdown），畫在
   條列**之後、按鈕列之前**；`points[].title` **維持純文字**（它是編號旁的標題列）。圖放內文 = 在 `body`
   寫 `![](assets/x.png)`；放最下面 = 寫在 `footer`。既有 app.json 一字不改仍有效（純文字是合法 markdown）。
   否決：整個換成一段 markdown `body`（版面交給作者的 markdown 功力，回歸面大）。
2. **圖放 `<app>/assets/<檔名>`，markdown 寫 `![](assets/x.png)`**：磁碟上的路徑就是 markdown 裡寫的。
   路由 `GET /apps/{slug}/assets/{name}`：`name` 純檔名（禁分隔符）、副檔名白名單同 icon、找不到 404；
   loader 抽成 `load_app_asset(slug, subdir, name)`，`load_app_icon` 改呼叫它（一個 loader，兩個路由）。
   無額外授權——圖是 App 的公開說明，跟 `/apps/{slug}` 同級。否決：圖直接放 `app.json` 旁邊（根目錄已有
   `app.json` / `icon.*` / `profiles/` / `workflows/`，截圖散進去很亂；`assets/` 之後也給別的東西用，不是
   onboarding 專用資料夾）。
3. **解析規則**（FE，`onboardingAssetUrl(scope, src)`）：app scope 且相對路徑以 `assets/` 開頭（可帶 `./`）
   → `/api/apps/{slug}/assets/<檔名>`；絕對路徑（`/…`）與 `http(s)://` **原樣通過**；其他相對路徑**原樣保留**
   （壞就壞得看得見，不猜）。平台層走絕對路徑（`web/public/onboarding/`，常數裡寫 `![](/onboarding/x.png)`）。
4. **`MarkdownBody` 多兩個可選參數**：`resolveUrl?: (src: string) => string`（有給就用它解析圖與連結、
   不碰 file service，所以 provider 外也能用）、`compact?: boolean`（加 `md-compact`）。不給就是今天的行為。
   不開第二條 markdown 管線。
5. **modal 寬度 460 不動**；圖 `max-width: 100%; height: auto`（`MarkdownBody` 既有）；沒有點擊放大。
   它是歡迎卡不是文件檢視器，截圖裁到重點；390px 手機反正滿版。
6. **要有一張真圖**：`_template/assets/` + `app.json` onboarding 一行 `![](assets/example.png)` 當語法範例
   （這是文件）；`rca/assets/` 放一張 Playwright 抓的真截圖並在 `rca/app.json` 用上（user 之後可換）——
   機制做完卻沒有任何 App 用到，就是「merged 但隱形」。
7. **平台層一起吃 markdown**：同一個元件，這部分沒得選；圖靠絕對路徑，不在元件裡分兩種行為。

## 不做的

- 圖片點擊放大 / lightbox。
- `points[].title` 的 markdown。
- 子目錄（`assets/a/b.png`）——跟 icon 一樣純檔名；要分類就用檔名。
- 外部圖片（`https://`）的可達性檢查——原樣通過，作者自己負責。
- 任何授權變更：`assets` 路由與 `/apps/{slug}` 同級。

## Phases（flat integer）

### P1 — 後端：`footer` 欄位 + `assets` 路由

- `apps/manifest.py`：`Onboarding.footer: str = ""`；`load_app_asset(slug, subdir, name) -> (bytes, media_type) | None`
  （純檔名 + 白名單 + `is_file`），`load_app_icon` 改成 `load_app_asset(slug, "", icon)`。
- `api/meta_routes.py`：`GET /apps/{slug}/assets/{name}`，形狀照 `/icon`（未知 app 404、缺檔 404）。
- **Tests（先紅）**：
  1. 放一個 png 到 tmp app 的 `assets/` → 200 + `image/png`。
  2. 未知 slug → 404；缺檔 → 404；`.txt` → 404（白名單）；`name` 含 `/`、`\`、`..` → 404（且不會讀到 `assets/`
     之外——用一個真的存在於上層的檔案做對照，證明拒絕不是「剛好找不到」）。
  3. 既有 icon 測試全綠（loader 換了，行為不變）；`Onboarding` 沒 `footer` 的 app.json 照常載入（預設 `""`）。

### P2 — FE 基礎：`MarkdownBody` 的兩個參數 + 解析器

- `renderers/MarkdownRenderer.tsx`：`MarkdownBody({text, path?, compact?, resolveUrl?})`；`resolveUrl` 有給
  → 圖與連結都經它；沒給 → 今天的 file-service 路徑。
- `lib/onboarding.ts`（或同層新檔）：`onboardingAssetUrl(scope: {kind: "platform"} | {kind: "app", slug}, src)`。
- **Tests（先紅）**：
  1. 沒有 `FileServiceProvider` + 有 `resolveUrl` → 能渲染、圖的 `src` 是 `resolveUrl` 的回傳。
  2. 沒有 `resolveUrl` 且沒 provider → 仍然 throw（今天的行為不變，pin 住）。
  3. `compact` → `article` 帶 `md-compact`。
  4. 解析器：`assets/x.png` / `./assets/x.png` → `/api/apps/rca/assets/x.png`；`/onboarding/x.png`、`https://…`
     原樣；`foo/x.png`、`x.png` 原樣；platform scope 一律原樣。

### P3 — FE modal：走 markdown

- `api/types.ts`：`Onboarding.footer?: string`。
- `OnboardingModal.tsx`：`intro` / `points[].body` / `footer` 改 `MarkdownBody compact resolveUrl=…`；
  `footer` 畫在條列後、按鈕前；`title` 不動。
- **Tests（先紅）**：
  1. `body: "**bold**"` → `<strong>`；`body: "![](assets/a.png)"` → `<img src="/api/apps/rca/assets/a.png">`。
  2. `footer` 存在時在條列之後、按鈕之前；不存在時不畫任何空容器。
  3. **parity**：把五個內建 app.json 的 onboarding 文字餵進去，`textContent` 與改前逐字相同（pm 的反引號那句
     除外——它現在多一個 `<code>`，字面仍同）。
  4. 平台層：`![](/onboarding/x.png)` 的 `src` 保持 `/onboarding/x.png`。

### P4 — 內容 + 真瀏覽器

- `apps/_template/assets/example.png`（小、無版權疑慮的佔位圖）+ `_template/app.json` onboarding 一個 point
  的 body 帶 `![](assets/example.png)`，`footer` 示範一句。
- `apps/rca/assets/<截圖>.png`（Playwright 抓 RCA dashboard 建立表單）+ `rca/app.json` 用上（版本號手動 bump，
  舊使用者會再看到一次——這是 #161 定義的語意）。
- 真瀏覽器 1280 與 390 各截一張：圖不超出 modal、按鈕列還在、可捲；390 下圖是滿版。

### P5 — 文件 + 運營方條目

- `docs/adding-an-app.md`：`app.json` 表補 `onboarding`（結構、markdown、`footer`、`assets/` 寫法、版本號
  語意），並新增 `assets/` 到目錄樹圖。
- `docs/migrations.md` 新條目（格式照 CLAUDE.md）：**行為**——onboarding 的 `intro` / `body` 現在當 markdown
  渲染，內建五個掃過無影響；**部署方自己的 App** 若 onboarding 文字含 `*` `_` `#` `[` `<`，畫面會變排版
  （`rollout 前` 掃一次；漏做的症狀：歡迎卡出現粗體/標題/連結不是作者要的）。**k8s · CI 側**——新增唯讀路由
  `GET /apps/{slug}/assets/{name}`，無新權限、無新 env。**設定 / 資料**——不動。

### P6 — review 與 CI（CLAUDE.md 新流程）

推（秒級 gate 過就推）→ **砍掉 push 觸發的 CI** → 四把鏡頭（符合度 / 真實性 / 缺陷 / 回歸）平行、各自
worktree → 乾淨後對最終 sha `gh run rerun` → 綠了報。三輪預算。

## As-built — review round 1（2026-09-19，四把鏡頭對 `e5f8bd5c`）

四份報告合併後先列表、再一次修（一格一條會紅的測試；每條守衛拿掉時**剛好**它那條紅，其餘綠）：

| # | 缺陷 | 幾把鏡頭 | 修法 | 釘子（突變 → 紅的那條） |
|---|---|---|---|---|
| A | `.md-body` 的 `color`/`font-size` 蓋掉 modal wrapper 的 inline 樣式（灰→黑、intro 14→13px）；`.md-compact p` 讓每個區塊尾端多 6px | 4 | `MarkdownBody` 加 `className`；modal 每個區塊傳 `onboarding-prose`；`base.css` 在 `.md-body.md-compact` **之後**加 `.onboarding-prose.md-body { color/font-size/line-height: inherit }` 與 `> :last-child { margin-bottom: 0 }`（同權重靠來源順序） | `onboardingProse.test.ts`（CSS 原文：三個 `inherit`、順序在 compact 之後、尾段 margin）；modal 的 DOM 測試（四個 article 都帶 `onboarding-prose md-body md-compact`，順便釘住 `compact`）。搬到 compact 之前 → 順序那條紅；modal 不傳 class → DOM 那條紅；拿掉 `color: inherit` / 尾段規則 → 各自那條紅 |
| B | `onboardingAssetUrl` 硬寫 `/api/`——`web/src` 唯一沒走 `API_PREFIX` 的後端 URL；`BASE_PATH=/my-svc/rca/` 的生產 overlay 下圖會打到 ingress 外 | 1（HIGH） | `${API_PREFIX}/apps/…`（同 `AppIcon.tsx`） | `onboardingAssets.deployBase.test.ts`（`vi.mock` `API_PREFIX=/my-svc/rca/api`）；改回 `/api` → 紅 |
| C | 超過 NAME_MAX 的檔名讓 `is_file()` 冒 `ENAMETOOLONG` → **500**（assets 路由是第一個把 URL 片段餵進 loader 的地方，任何人可構造） | 2 | `is_file()` 的 `OSError` 一律 `None`（Python 3.13 的 `is_file` 本來就這樣；255 是檔案系統常數，zip Traversable 沒有，不放在名字守衛） | `test_load_app_asset_treats_a_name_the_filesystem_refuses_as_no_asset`（loader `None` + route 404）；拿掉 `except` → 只有它紅 |
| D | `name in {".", ".."}` 是死守衛：兩者副檔名是 `.`，白名單先擋 | 1 | 拆掉，docstring 說明由白名單擋 | loader 測試多一個 `"."`；`..`/`.` 仍 `None` |
| E | 決策 4「有 `resolveUrl` 就用它」只釘一半：provider 與 `resolveUrl` 同時在時誰贏沒測 | 1 | 只加測試 | `MarkdownRenderer.test.tsx`：provider 內帶 `resolveUrl` → 用 `resolveUrl`；改成 provider 優先 → 紅 |
| F | assets 路由的未知 slug 守衛沒有敏感測試（`nope` 沒守衛也 404） | 1 | 只加測試 | `test_get_app_asset_serves_only_folders_that_are_apps`：`_template/assets/example.png` 磁碟上有、不是 App → 404；拿掉守衛 → 只有它紅 |
| G | parity 測試把 pm 的反引號句跳過了（plan 點名要「去反引號後在」） | 1 | 只加測試 | modal 測試從 `pm/app.json` 讀那句：去反引號後在 `textContent`，`<code>` 剛好一個 = `issues/N.md` |
| H | `_template/assets/example.png` 的文字有 tofu 方塊（字型缺箭頭 glyph） | 2 | 純 ASCII 重生（640×240，8.7 KB） | 目視 |
| I | `test_get_app_asset_route_never_sees_a_path_shaped_name` 的 `../icon.png` 那格是 httpx 客戶端先正規化（送出的是 `/apps/rca/icon.png`），對任何 handler 都會過 | 1 | 刪那格、docstring 說明；留 `..%2F` 兩格（spy 證明 handler 沒被叫） | — |
| J | 文件：runbook 的「會變排版的字元」漏 `$`（pipeline 有 remark-math，`$5 and $10` 會畫成 KaTeX）、多列 `<`（沒 rehype-raw，`<b>` 是純文字）；`adding-an-app.md` 漏 `jpeg`；`MarkdownRenderer.tsx` docstring「Two callers」 | 2 | 改字 | — |

修後真瀏覽器重量（`/a/rca`，Chromium，light 1280×900 / 390×844、dark 1280×900）：四個 article（intro + 三個
body）的 computed `font-size` = wrapper（14 / 13 / 13 / 13 px）、`color` = 「Don't show again」按鈕的
`--text-paper-d`（light `rgb(92,95,102)`、dark `rgb(169,173,181)`）≠ 標題色（`rgb(26,27,31)` / `rgb(236,234,227)`），
每個 article 的最後一個子元素 `margin-bottom: 0`。

## 驗證（DoD）

- 每個 phase：targeted tests + `ruff check` / `ruff format --check` / `ty check`（後端）、`pnpm typecheck` +
  vitest（前端）。
- 每條守衛做一次突變：拿掉白名單 / 拿掉分隔符檢查 / 拿掉 `resolveUrl` 分支 / footer 不畫——對應的那一條測試必紅。
- 真入口：起 app、開 RCA dashboard、看到歡迎卡裡的截圖；1280 與 390 兩張截圖附在 PR。
- migrations 條目在同一個 PR 內。
