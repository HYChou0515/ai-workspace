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
- `.md-body` 樣式是**全域**的（`web/src/styles/base.css:233`），已有 `md-compact` 變體（13px 級距，跟 modal
  現在的字級一致）——不是被 scope 住的 class，沿用不會零樣式。
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

## 驗證（DoD）

- 每個 phase：targeted tests + `ruff check` / `ruff format --check` / `ty check`（後端）、`pnpm typecheck` +
  vitest（前端）。
- 每條守衛做一次突變：拿掉白名單 / 拿掉分隔符檢查 / 拿掉 `resolveUrl` 分支 / footer 不畫——對應的那一條測試必紅。
- 真入口：起 app、開 RCA dashboard、看到歡迎卡裡的截圖；1280 與 390 兩張截圖附在 PR。
- migrations 條目在同一個 PR 內。
