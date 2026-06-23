# Plan — #93: KB 視圖 URL 化（react-router-dom，不換 library）

## 背景與重新定義

Issue 標題：「使用 tanstack react router 讓所有常用 page 都有自己的 url」。

grill-me 發現：前端**已經在用 `react-router-dom` v7.15.1**，頂層頁面（`/`、`/a/:slug`、
`/a/:slug/:itemId`、`/kb`、`/kb/doc/*`、`/diagnostics`）早就 URL 化了。`routes/` 底下那套
TanStack Router 檔案只是 AutoCRUD admin 的產物，主 App 沒在用。

真正的缺口是 **KB 內部導覽全靠 `useState`**：哪個 collection、哪個 tab、哪篇 doc/card/wiki、
哪個 chat、citation 浮層，重整或分享就回到預設視圖。

**鎖定決策（grill-me）**

1. **目標 = URL 涵蓋率**，不是 library 抽換。
2. **留在 react-router-dom v7**，不換 TanStack Router（換 library 是疊加在「加路由」之上的純內部成本，
   使用者零感知；達成本清單根本不需要換）。issue 實質重新詮釋為「URL 涵蓋」。
3. **巢狀階層路徑**、段名用**可讀**風格（`/kb/collections/:cid`，非 `/kb/c/:cid`）。
4. doc / wiki 葉節點是**含斜線的檔案路徑** → 用 **splat 段**（與既有 `/kb/doc/*` 一致）；card 是
   opaque 無斜線 id → 用 `:cardId` 單段。
5. citation / doc 浮層 **URL 化**，機制 = **shell 層 search param `?doc=<id>&hl=<snippet>`**
   （疊在當前頁、上一頁關閉、可分享）。
6. 範圍 = **KB 頁面清單 + KB 列表 filter 進 URL**；topic-hub / AppDashboard **不動**。

## 路由表（最終）

```
/kb                                          → redirect /kb/collections
/kb/collections                              collections grid
        ?view=all|mine|pinned&owner=…&q=…    grid filter（search param）
/kb/collections/:cid                         → redirect …/documents（避開與 cards/wiki 段名衝突）
/kb/collections/:cid/documents               documents tab（KbDocIde，無開檔）
/kb/collections/:cid/documents/*             開某篇 doc（splat = KbDocIde activePath）
/kb/collections/:cid/cards                   context cards tab
/kb/collections/:cid/cards/:cardId           開某張 card（ContextCardsTab draft）
/kb/collections/:cid/wiki                    wiki tab
/kb/collections/:cid/wiki/*                  開某篇 wiki page（splat = KbWikiIde activePath）
/kb/chats                                    chats 列表（取代既有 ?tab=chats）
        ?view=all|pinned|shared              chats filter（search param）
/kb/chats/new                                新對話（首則訊息後 navigate replace → 真 id）
/kb/chats/:chatId                            單一對話
/kb/doc/*                                    （既有）全頁唯讀 doc：開新分頁 / citation 分享目標 — 保留不動

任何 /kb/... 路徑可帶 ?doc=<sourceDocId>&hl=<snippet> → KbDocViewer 浮層疊在當前頁
```

### 識別碼與編碼

- **collection** `resource_id`：無斜線、URL-safe 單段（防禦性 `encodeURIComponent` 即可）。
- **card** `id`：opaque specstar id，無斜線、URL-safe 單段。
- **doc / wiki page**：`activePath` 是含斜線的 canonical 路徑（`/dir/x.md`）→ 走 splat（`*`）段，
  前導斜線去除、子斜線保留：`/kb/collections/c1/documents/dir/x.md`。
- **`?doc=` 浮層**：永遠是 **SourceDoc opaque id**（不是 path），`encodeURIComponent` 一次；
  **不解析** id（守 CLAUDE.md「Never parse it」）。「開新分頁」仍指向 `/kb/doc/*`。

## 維持本地 state（不進 URL）

URL 記「你在看什麼」，不記「暫時的編輯狀態」：rename / description 草稿、icon picker、confirm 刪除、
retrieval modes 面板、card 的 edit/preview 切換、doc/wiki IDE 的 edit mode 與未存 buffer（FileBuffer）。

## 結構改動

- `KbHome` → **layout route**：左 nav（Collections / Chats 改 `<Link>`/`NavLink`）+ topbar +
  `AskAgentLauncher` + `?doc=` 浮層 + `<Outlet/>`。不再持有 `tab` / `chatId` / `viewer` state。
- `KbCollectionsPage` → 拆成 **grid**（`/kb/collections`）與 **collection-page layout**
  （`/kb/collections/:cid`，含 header/stats/tabs + `<Outlet/>`）。`selectedId` / `collectionTab` 由 URL 取代。
- 各 tab 內部 `setActivePath` / `setDraft` 改由 route param 驅動 + `navigate()` 寫回。
- grid / chats 的 filter（`tab`/`ownerFilter`/`colQuery`）改讀寫 `useSearchParams`。

## 測試策略（FE TDD，vitest）

- 既有 `QueryWrap` / `renderWithQuery`（`web/src/test/queryWrapper.tsx`）不動。
- 新增 `renderWithRoute(ui, { path, route })`：`MemoryRouter initialEntries=[path]` + QueryClientProvider，
  讓測試能以「進到某 URL」起始並斷言 `useNavigate` 後的 location。
- App.tsx 已是 router-agnostic（host 提供 router）——測試掛 `MemoryRouter`。
- 逐路由紅綠；改 setState→navigate 的元件，測「點擊後 URL 變化」「deep-link 進入正確視圖」。
- 收尾跑全套 + 100% coverage gate（依 targeted-tests-then-full），FE 另跑 `pnpm typecheck` + `pnpm build`。

## Phases（flat）

- **P1** — KB shell 變 layout；`/kb`→`/kb/collections` redirect；grid 落在 `/kb/collections`，
  filter（`view`/`owner`/`q`）讀寫 search param；點 card → `navigate(/kb/collections/:cid)`。
- **P2** — collection-page 變巢狀 layout；`/kb/collections/:cid`→`…/documents` redirect；
  tabs（documents/cards/wiki）成子路由 `<Link>`；stale wiki tab fallback 維持。
- **P3** — documents 葉路由 `…/documents/*` 驅動 `KbDocIde.activePath`（開檔 navigate、deep-link 開檔）。
- **P4** — cards 葉路由 `…/cards/:cardId` 驅動 `ContextCardsTab.draft`。
- **P5** — wiki 葉路由 `…/wiki/*` 驅動 `KbWikiIde.activePath`。
- **P6** — chats 路由：`/kb/chats`（filter `?view=`）、`/kb/chats/new`、`/kb/chats/:chatId`；
  取代 `chatId` state 與 `?tab=chats`；新對話首訊後 `navigate(replace)` 成真 id（不可 mid-stream remount）。
- **P7** — citation / doc 浮層改 `?doc=`：shell 監看 search param 開 `KbDocViewer`；所有開浮層處
  （chat citation、AskAgentLauncher、WikiBrowser onOpenDoc）改 `navigate` 加 `?doc=`；關閉移除 param。
- **P8** — 清掉死 state；`?tab=chats` 舊連結 redirect 相容；全套測試 + 100% coverage + tsc + build 收尾。

## 不在範圍

- 不換成 TanStack Router。
- topic-hub（`/a/topic-hub/:itemId`）item chat 選取、AppDashboard 的 severity/owner/age filter 不 URL 化。
- 後端不動（純 FE）。
