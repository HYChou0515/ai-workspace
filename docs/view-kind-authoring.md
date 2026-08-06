# 寫一個 View Kind（給維運方）

你可以自己寫一種畫面，讓 workspace 裡的 `*.ai.yaml` 檔案用它來呈現資料。你寫一個 React
元件、在 `web/src/ext/` 加一行註冊，就結束了——**平台這邊不需要為你改任何程式碼**。

這頁只講你要做的事。

!!! info "先講清楚：這不是熱插拔"
    前端是**一包編譯出來的 bundle**。你的程式碼要進到那次 build 才會生效，所以流程是
    「開 PR → 合併 → 重新 build → 重新部署」。把檔案丟到執行中的機器上**不會**有任何效果。

---

## 1. 你要交付什麼

兩個東西，都在 `web/src/ext/`：

```
web/src/ext/
  WaferMapView.tsx      # 你的元件
  index.ts              # 加一行 registerViewKind({...})
```

`web/src/main.tsx` 已經有 `import "./ext";`，所以只要 `index.ts` 註冊了，你的 kind 就上線了。
**你不需要動 `ext/` 以外的任何檔案**——如果你覺得非動不可，那是我們的接口缺了東西，開 issue 找我們。

## 2. 最小可動範例

一個把 workspace 裡的 CSV 畫成表格的 kind。**權威版本是 repo 裡的**
[`web/src/ext/CsvTableView.tsx`](https://github.com/HYChou0515/ai-workspace/blob/master/web/src/ext/CsvTableView.tsx)——它跟著測試一起跑 CI。
下面是同一份程式碼的節錄（省略了錯誤訊息的樣式），**以檔案為準**：

```tsx
// web/src/ext/CsvTableView.tsx
import { DataGrid, type EntityViewProps, parseCsv, useFileBuffer, viewParamString } from "../renderers/entity/public";

// 讀檔的部分獨立成一個元件，這樣「沒有 source」的情況可以在父層直接 return，
// 不會變成有條件呼叫 hook（React 不允許）。
function CsvFromFile({ path }: { path: string }) {
  const { entry } = useFileBuffer(path);
  if (entry.status === "loading") return <div>Loading {path}…</div>;
  if (entry.status === "error") return <div>{entry.error ?? `could not read ${path}`}</div>;
  const delimiter = path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
  return <DataGrid rows={parseCsv(entry.text, delimiter)} />;
}

export function CsvTableView({ spec }: EntityViewProps) {
  // `source` 是你自己的 key，不在 ViewSpec 上 —— 用 viewParamString 讀
  const source = viewParamString(spec, "source")?.trim() ?? "";
  if (!source) return <div>This view needs a `source:`.</div>;
  return <CsvFromFile path={source} />;
}
```

```ts
// web/src/ext/index.ts
import { registerViewKind } from "../renderers/entity/public";
import { CsvTableView } from "./CsvTableView";

registerViewKind({ kind: "csv-table", Component: CsvTableView });
```

使用者那邊放一個 view 檔就會生效：

```yaml
# /views/yield.ai.yaml
view: csv-table          # 對應你註冊的 kind
title: Wafer yield       # 面板標題（可省略）
source: /data/wafer.csv  # 這是「你自己的」key，見下一節
```

`kind` 撞名會**直接丟例外**（開機就爆，不是靜默覆蓋）——兩個元件搶同一個 `view:` 沒有正確答案，
而靜默的勝負會取決於 import 順序。平台保留的名字（目前是 `health`，由容器自己接手渲染）
同樣會丟例外，而不是讓你註冊成功卻永遠畫不出來。取名建議加自己的前綴，例如 `acme-wafermap`。

**你的元件 throw 不會弄垮整個 app。** renderer 外面包了一層 error boundary，壞掉時只有那個面板
變成一則錯誤訊息，其餘畫面照常；完整的 stack 會進 console。

## 3. 你的資料從哪來

有兩種來源，**你可以只用檔案那一種**（多數情況就是這樣）。

### 3.1 你自己的設定：`spec`

`spec` 是那份 `.ai.yaml` 解析後的內容。平台認得的 key（`view` / `title` / `entity` / `columns`…）
有明確型別，而且**會被強制轉型**——那份 YAML 是使用者手寫的，所以 `title:` 寫成一個 mapping 時
你拿到的是 `undefined`，不是一個會讓 React 當場爆掉的物件。

**你自己加的 key 不在 `ViewSpec` 型別上**（放上去會讓平台自己每個欄位都失去錯字檢查），
用存取器讀：

```tsx
const source = viewParamString(spec, "source")?.trim() ?? "";   // ✅ 字串或 undefined
const raw = viewParam(spec, "options");                          // ✅ unknown，自己收窄
```

這兩個存取器回傳的是**原始 YAML 文件**的值，不是平台轉型後的版本。所以就算你的 key 剛好跟
平台的撞名——`columns`、`card`、`sort`、`title`、`label`、`span`、`group_by`、`week`、
`entity`、`hidden_fields`、`skip_weekends`、`assignee`、`assignee_display`——你讀回來的
仍然是你寫下去的東西。（不過還是**建議避開這些名字**，因為平台可能也會拿它們去畫東西。）

### 3.2 Workspace 檔案

這是主要來源。兩個工具：

| 用法 | 什麼時候用 |
|---|---|
| `useFileBuffer(path)` | 讀單一檔案。有快取，別人／agent 改了會自動更新。**首選。** |
| `useFileService()` | 需要列檔（`listFiles(prefix?)`）、寫檔（`writeFile`）、或組出 `fileUrl(src)` 給 `<img>` 用 |

`useFileBuffer` 回傳的 `entry.status` 是 `"loading" | "ready" | "error"`，三種都要處理——
`ready` 時才有 `entry.text`。

路徑用 workspace 的絕對路徑（開頭 `/`），跟檔案樹看到的一樣。

### 3.3 Entity（可省略）

只有當你的 kind 要畫「entity 紀錄」（issue、milestone 這類結構化紀錄）時才需要。你要在註冊時
宣告 `needsEntity: true`，那份 view 檔就必須寫 `entity:`；沒寫的話使用者會看到一則明確的提示。

**判準是那份 view 檔有沒有寫 `entity:`，不是你有沒有宣告 `needsEntity`。** `needsEntity` 只
決定「沒寫 `entity:` 時要不要擋下來」。所以一份寫了 `entity:` 的檔案，即使你的 kind 沒宣告
`needsEntity`，下面這些 props 一樣會有內容。**view 檔沒寫 `entity:` 時它們才是空的**——那是
正常的，不是壞掉：

| prop | 是什麼 |
|---|---|
| `entities` | 紀錄陣列。view 檔沒寫 `entity:` ⇒ `[]` |
| `type` | 該 entity 的 schema（欄位、role、表單）。沒有就是 `null` |
| `onCreate` / `onPatch` | 寫入用。**要改 entity 一律走這兩個**，不要自己打 API |
| `canWrite` | 見下一節 |
| `refIndex` / `users` | 關聯紀錄索引 / 使用者名冊，畫 assignee 之類的東西用 |

## 4. 邊界

**只從 `renderers/entity/public` import。** 這條規則有測試在守（`web/src/ext/imports.test.ts`），
違反會讓 CI 變紅。理由在下一節。

**讀寫檔一律經 `FileService`**，不要自己 `fetch`——權限、scope、快取都在那層。

**`caps` 不是權限。** 兩個容易搞混的東西：

- `useFileService().caps`：**這個介面**支不支援某個操作（例如 KB 文件頁不能寫）
- props 上的 `canWrite`：**這位使用者**對這個 item 有沒有寫入權

真正的強制在後端。你的寫入按鈕要看 `canWrite` 決定顯不顯示，否則你會畫出一顆按下去被伺服器
擋回來的按鈕。

## 5. 本機跑起來看

```bash
cd web && pnpm install
pnpm run dev          # 5173，API 會 proxy 到後端
pnpm vitest run src/ext   # 你的測試
```

在任何一個 item 的 workspace 裡建一個 `/views/xxx.ai.yaml`，內容照上面第 2 節，
從檔案樹點開它就會看到你的畫面。

**你的 app 不需要有 entity 型別**——只讀檔案的 kind 在完全沒有 `.entity/` 的 app（例如 rca）
一樣能用。

## 6. 交付流程

程式碼放在**我們的 repo** 裡，跟平台跑同一套 CI（`pnpm run typecheck`、`pnpm vitest run`、
`pnpm run build`）。

1. 開分支，只動 `web/src/ext/`
2. 補測試——建議照 [`CsvTableView.test.tsx`](https://github.com/HYChou0515/ai-workspace/blob/master/web/src/ext/CsvTableView.test.tsx)
   從**檔案內容**進去測，那才是使用者真正走的路徑
3. 開 PR。**（待設定）** 目標是把 `web/src/ext/` 掛進 CODEOWNERS，讓只動這個資料夾的 PR 由你們
   自己審、不必排隊等平台。這需要一個實際的 GitHub team handle，還沒建立——在那之前照一般流程送審
4. 合併後隨下一次部署上線

## 7. 相容性

**沒有版號，我們也不承諾 `public` 這個介面不變。**

換來的是：你的程式碼跟我們在同一個 CI 裡編譯，所以我們改壞你的時候，會在**編譯期就紅**，
然後一起修——而不是等你的使用者打開畫面才發現。

這就是「只從 `renderers/entity/public` import」那條規則的用意：它讓「改這個會影響誰」在我們
動手的當下就看得見。繞過去 import 內部路徑，這個保護就沒了，壞掉要自己處理。

目前還在變動、建議先別依賴的部分：`ViewConfig`（表格／甘特的齒輪面板設定）還在長新欄位。

---

## 相關

- [擴充平台：Tools / Skills / Workflows](extending-the-platform.md)——其他擴充面
- [寫一支工具（外部作者）](tool-authoring.md)——不需要我們 repo 權限的那條路（工具，不是畫面）
- `web/src/renderers/README.md`——檔案預覽 renderer（另一層，目前尚未開放第二方註冊）
