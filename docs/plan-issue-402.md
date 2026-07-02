# Plan — Issue #402: doc filetree filter + resizable width

> KB collection、doc filetree 需要有 filter。

## Scope (grill-locked)

盤點發現 **collection 清單其實已經有搜尋框**(`KbCollectionsGrid.tsx`,URL 參數 `q`,只比對 name),
所以 #402 真正的工作重心在 **doc filetree**。使用者確認範圍:

1. **只做 doc filetree 的 filter** — collection 清單完全不動。
2. **另加:filetree 寬度可拖拉伸縮**(使用者於 grill 中補充的需求)。

## Locked decisions

| # | 決策 | 選定 |
|---|------|------|
| Q1 | 範圍 | 只做 doc filetree filter(collection grid 不動) |
| Q2 | filter 顯示位置 | KB **doc IDE + wiki IDE** 都開;調查工作區**不開**。FileTree 加 optional prop 開啟 |
| Q3 | 比對範圍 | **完整相對路徑 substring、不分大小寫**、純前端過濾(不打後端;內容搜尋是 SearchPanel 的事) |
| Q4 | 樹狀行為 | 命中檔案的**祖先資料夾保留並自動展開**;隱藏不含命中的資料夾;**清空搜尋字還原**原本折疊狀態 |
| Q5 | 輸入框位置 | 塞進 sticky "Files" header **同一列**;靠「可調寬度」解決 header 擁擠 |
| Q6 | 寬度伸縮 | **只做可拖拉伸縮**(不加折疊 chevron);沿用現成 `ResizeDivider` + `usePersistentNumber` |
| Q7 | 寬度記憶 | doc + wiki **共用一個 key** `kb:ide:treeWidth`(default 260, min 160, max 560) |

## Existing building blocks (重用,不重造)

- `web/src/pages/investigation/FileTree.tsx` — 共用檔案樹元件(workspace / KB doc / KB wiki 三處共用)。
  sticky header 在 ~329–468,樹本體 ~473–581,`visibleOrder`/折疊 state ~77。
- `web/src/pages/investigation/fileTree.ts` — `buildFileTree(files, dirs) → TreeNode[]`(純函式接縫)。
- `web/src/components/ResizeDivider.tsx` — 現成拖拉分隔線(pointer capture、delta-from-start、可選 collapse chevron)。
- `web/src/hooks/usePersistentNumber.ts` — `usePersistentNumber(key, default, min, max)`,localStorage 持久化 + clamp。
  precedent:`usePersistentNumber("rca:layout:sidebar", 260, 180, 560)`。
- 佈局:`.kb-ide__main`(kb.css:2239)目前是 `grid-template-columns: minmax(180px,260px) 1fr`(tree 寫死)。
  KbDocIde 與 KbWikiIde 的 wrapper 類別**完全相同**(`.kb-ide__main` > `.kb-ide__tree` + `.kb-ide__pane`)。
- filter 風格參考:`CollectionsChecklist.tsx`(controlled input + client `.filter()`);`kb-docsearch` CSS class。

## Phases (flat integer;走 /tdd,一 phase 一 commit)

### Phase 1 — 純函式:tree 過濾邏輯(fileTree.ts)
在 `fileTree.ts` 新增可測的過濾函式,例如:
```
pruneTree(tree: TreeNode[], term: string): { tree: TreeNode[]; expand: Set<string> }
```
- `term` 空 → 原樹、`expand` 空(呼叫端維持原折疊)。
- 命中判定:node.path.toLowerCase().includes(term.toLowerCase())。
- 保留任何「自己命中」或「有後代命中」的節點;命中檔案的所有祖先資料夾 path 收進 `expand`。
- **測試先行**:空字串、命中檔名、命中資料夾名(其下全展開)、無命中(空樹)、大小寫、巢狀祖先展開。
- 檔案:`fileTree.ts`、`fileTree.test.ts`。

### Phase 2 — FileTree filter UI(opt-in prop)
`FileTree.tsx` 加 optional prop（如 `searchable?: boolean`）：
- header 同一列加 controlled search input(search icon + 清除 X,i18n placeholder,沿用 `kb-docsearch` 風格)。
- 內部 `const [q, setQ] = useState("")`;用 Phase 1 的 `pruneTree` 產生顯示樹 + `expand` 集合。
- filter 生效時:以 `expand` 覆蓋折疊狀態(強制展開祖先);清空還原原本 `collapsed` state。
- workspace 呼叫端**不傳** `searchable` → 行為不變(回歸保護)。
- **測試**:`FileTree.test.tsx` — 有 prop 才出現輸入框、輸入後只剩命中+祖先、清空還原、workspace(無 prop)無輸入框。
- 檔案:`FileTree.tsx`、`FileTree.test.tsx`、i18n key(`lib/i18n.tsx`)。

### Phase 3 — 可拖拉寬度(KB doc + wiki IDE)
把 `.kb-ide__main` 由固定 grid 改為 flex 佈局 + `ResizeDivider`:
- kb.css:`.kb-ide__main` → `display:flex`;`.kb-ide__tree` 拿掉固定 grid 寬,改由 inline `style.width` 控制;pane `flex:1`。
- KbDocIde.tsx / KbWikiIde.tsx:
  - `const [treeW, setTreeW] = usePersistentNumber("kb:ide:treeWidth", 260, 160, 560)`(共用 key)。
  - tree `<div style={{ width: treeW, flexShrink: 0 }}>`;tree 與 pane 間插入
    `<ResizeDivider orientation="vertical" onResizeStart onResize={(d)=>setTreeW(start+d)} />`。
  - 兩個 IDE 同時傳 `searchable` 開 Phase 2 的 filter。
- **測試**:兩個 IDE 都渲染 ResizeDivider + FileTree 帶 searchable;拖拉更新寬度;共用 key 持久化。
- 檔案:`KbDocIde.tsx`、`KbWikiIde.tsx`、`styles/kb.css`、對應 `.test.tsx`。

### Phase 4 — 收尾與門檻
- 全前端 `pnpm run typecheck`;涉及檔 vitest 綠。
- 後端未動(純 FE),但仍跑一次 lint/format 檢查前端。
- 手動/Playwright 快速驗證:doc IDE 開 collection → 輸入過濾字 → 樹只剩命中+祖先展開 → 清空還原;拖拉分隔線調寬度、reload 後保留;wiki IDE 同樣行為;workspace filetree **不受影響**(無 filter、寬度行為不變)。

## Non-goals / 明確不做

- collection 清單的 filter(已存在,不動)。
- 檔案**內容**搜尋(那是 workspace SearchPanel 的事)。
- 折疊隱藏 tree(chevron)——日後想要再開 issue。
- 調查工作區 filetree 加 filter(工作區已有 SearchPanel;寬度也早已可調)。
- 後端 / specstar 改動(#402 為純前端)。
