# 寫一個 View Kind（給維運方與 plugin 作者）

你可以自己寫一種畫面，讓 workspace 裡的 `*.ai.yaml` 檔案用它來呈現資料。做法是寫一個
**runtime view plugin**：一個資料夾，維運方把它放進 plugin 目錄、重啟 app，就上線了——
**不用改平台的程式碼，也不用重新 build SPA**（#847/#848）。

原始碼放在這個 repo 的 `view-plugins/<name>/` 時，`web/` 的型別檢查與測試會一起涵蓋它、
CI 與映像也會建它（本頁的工具都以此為預設）。放在別處也能 build、能安裝，只是沒有這些檢查。

這頁只講你要做的事。

!!! warning "信任模型"
    plugin 在 SPA 的 origin 裡、以登入者的**全部權限**執行。它是**維運方安裝、維運方信任**的程式，
    從來不是使用者上傳的東西。使用者自己寫的頁面走 WUI 的 iframe 沙箱，那是另一條路。

---

## 1. 一個 plugin 長什麼樣

原始碼放在 repo 的 `view-plugins/<name>/`，裝好之後在 plugin 目錄裡是：

```
<plugin 目錄>/<name>/
  plugin.json       # 名字、SDK 版本、kind、給 agent 的索引行、skill、沙盒半邊
  web/index.js      # 建好的 ES module；import 時呼叫 registerViewKind
  sandbox/          # 選配：一個標準的 prebuilt tool bundle（有 `launch`）
  skill/SKILL.md    # 選配：給會畫 view 的 agent 的 skill
  scenarios/        # 選配：`view_plugin tune` 用的 skill_eval 情境，不會複製進 workspace
```

plugin 目錄是 `view_plugins.dir`（空 ⇒ `$WORKSPACE_VIEW_PLUGINS_DIR` ⇒ `<repo>/.view-plugins`），
見 [設定](configuration.md)。目錄不存在 = 沒有 plugin；**任何一個 plugin 壞掉就拒絕開機**，
訊息會點名是哪個 plugin 的哪個欄位。

### `plugin.json`

```json
{
  "name": "chart",
  "sdk": "1",
  "kinds": ["chart"],
  "views": [{ "kind": "chart", "when": "numbers across categories or over time, from a table file" }],
  "skill": "skill",
  "sandbox": { "bundle": "sandbox", "validate": true }
}
```

| 欄位 | 規則 |
|---|---|
| `name` | 小寫 slug（`[a-z0-9][a-z0-9_-]*`），**必須等於資料夾名** |
| `sdk` | 這個 plugin 是對哪個 SDK **主版號**建的（字串）。和 app 的 `SDK_VERSION` 主版號不同 ⇒ 不載入，面板上說明原因 |
| `kinds` | 至少一個；不能重複、不能用內建的 `table` / `board` / `gantt` / `health` / `wui`、不能和別的 plugin 撞 |
| `views` | 選配。每一條變成 agent prompt 裡 `## Available views` 的一行 `` - `kind`: when ``；`kind` 必須是自己的 |
| `skill` | 選配。plugin 裡一個有 `SKILL.md` 的資料夾；`SKILL.md` 的 `name` 必須等於 plugin 名 |
| `sandbox` | 選配。`{"bundle": "<資料夾>"}` 或 `{"artifact": "<#674 artifact URL>"}` **二擇一**；`"validate": true` 見第 6 節 |

**未知的 key 一律拒絕**（跟設定檔 loader 同一條規則）——拼錯的 key 靜靜地不生效，比開機失敗更難查。

## 2. 最小可動範例

一個把 workspace 裡的 CSV 畫成表格的 kind。**權威版本是 repo 裡的**
[`view-plugins/csv-table/`](https://github.com/HYChou0515/ai-workspace/tree/master/view-plugins/csv-table)——
它跟著測試一起跑 CI，也是 app 映像裡預設就裝好的 plugin。下面是節錄，**以檔案為準**：

```tsx
// view-plugins/csv-table/web/src/CsvTableView.tsx
import { DataGrid, type EntityViewProps, parseCsv, useFileBuffer, viewParamString } from "@aiws/view-sdk";

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

```tsx
// view-plugins/csv-table/web/src/index.tsx —— SPA import 的就是這支建出來的 index.js
import { registerViewKind } from "@aiws/view-sdk";
import { CsvTableView } from "./CsvTableView";

registerViewKind({ kind: "csv-table", Component: CsvTableView });
```

使用者那邊放一個 view 檔就會生效：

```yaml
# /views/yield.ai.yaml
view: csv-table          # 對應你註冊的 kind
title: Wafer yield       # 面板標題（可省略）
source: /data/wafer.csv  # 這是「你自己的」key，見第 4 節
```

`kind` 撞名會**直接丟例外**——兩個元件搶同一個 `view:` 沒有正確答案。取名建議加自己的前綴，
例如 `acme-wafermap`。

**你的元件 throw 不會弄垮整個 app。** 面板外面包了一層 error boundary，壞掉時只有那個面板
變成一則錯誤訊息，其餘畫面照常；完整的 stack 會進 console。錯誤訊息附一顆 **Retry**——因為
你讀的資料檔平台看不到，使用者在別處把它修好之後，需要一個明確的重試出口。

**載入失敗也一樣只影響自己的面板。** plugin 的 `import()` 丟例外、逾時（15 秒）、SDK 主版號不合、
或是 import 完卻沒註冊它宣告的 kind——app 照樣開起來，每個用到那些 kind 的 `*.ai.yaml` 會顯示
一則點名 plugin 與原因的錯誤。沒人宣告的 kind 仍然是 "Unsupported view kind"。第一次 render 會等
plugin 載入：清單請求與每個 plugin 的 import 各有 15 秒上限，plugin 之間平行載入。

⚠️ **`spec` 每次 render 都是新物件。** 別把它放進 `useEffect` 的相依陣列（會每次都觸發）。
要相依就相依你真正讀出來的值，例如 `viewParamString(spec, "source")`。

## 3. 建置：`@aiws/view-sdk` 與 React 由 app 提供

plugin 的 web 半邊是**一支 ES module**（`web/index.js`），用 Vite 的 lib 模式、**production** 建：

- `react`、`react/jsx-runtime`、`react-dom`、`react-dom/client`、`@aiws/view-sdk` 一律標成
  **external**。app 的 import map 把這些名字指到 app 自己的那一份——你的元件用的是 app 的 React、
  app 的 view 註冊表。
- **把 React 打包進去會壞**：你的 hook 會跑在一份沒 render 過任何東西的 React 上，第一次 render 就丟
  `Cannot read properties of null (reading 'useState')`。
- **development build 也會壞**：它 import `react/jsx-dev-runtime`，import map 不提供。
- **`process.env` 要換掉**：Vite 的 lib 模式**不會**替換 `process.env.NODE_ENV`，打包進去的第三方函式庫
  （例如圖表庫）的 dev 檢查會在瀏覽器丟 `process is not defined`——node 跑的測試看不到。在
  `vite.config.ts` 加 `define: { "process.env.NODE_ENV": JSON.stringify("production") }`。

`view_plugin check` 三種都會擋下來（見第 8 節）。`view_plugin new` 產生的 `vite.config.ts` 已經設好。

你自己的 runtime 依賴（例如圖表函式庫）照常放進 plugin 的 `package.json`，會被打包進 `index.js`。
**不要依賴和 `web/` 不同版本的同一個套件**：plugin 的原始碼與測試是由 `web/` 的 `tsc` 與 vitest 檢查的
（bare import 先從 `web/` 解析、找不到才用 plugin 自己的 `node_modules`），所以你會對著 `web/` 的版本測、
卻出貨自己的版本。

## 4. 你的資料從哪來

三種來源，**你可以只用檔案那一種**（多數情況就是這樣）；要算東西時再加沙盒（第 5 節）。

### 4.1 你自己的設定：`spec`

`spec` 是那份 `.ai.yaml` 解析後的內容。平台認得的 key（`view` / `title` / `entity` / `columns`…）
有明確型別，而且**會被強制轉型**——那份 YAML 是使用者手寫的，所以 `title:` 寫成一個 mapping 時
你拿到的是 `undefined`，不是一個會讓 React 當場爆掉的物件。

**你自己加的 key 不在 `ViewSpec` 型別上**（放上去會讓平台自己每個欄位都失去錯字檢查），
用存取器讀：

```tsx
const source = viewParamString(spec, "source")?.trim() ?? "";   // ✅ 字串或 undefined
const raw = viewParam(spec, "options");                          // ✅ unknown，自己收窄
const whole = viewDocument(spec);                                // ✅ 整份文件（一份複本）
```

`viewDocument(spec)` 給你**整份**解析後的文件（每個 key，照寫的樣子，一份 `structuredClone` 的
複本——改它不會影響平台手上的 spec）。要「未知的 key 當錯誤」時用它：`viewParam` 只能讀你已經知道
名字的 key，找不到拼錯的那一個。

這三個存取器回傳的是**原始 YAML 文件**的值，不是平台轉型後的版本。所以就算你的 key 剛好跟
平台的撞名——`view`、`entity`、`title`、`columns`、`card`、`sort`、`hidden_fields`、
`group_by`、`span`、`label`、`assignee`、`assignee_display`、`skip_weekends`、`week`、
`schedule`——你讀回來的仍然是你寫下去的東西。（不過還是**建議避開這些名字**，因為平台
也會拿它們去畫東西。）

### 4.2 Workspace 檔案

這是主要來源。**讀單一檔案首選 `useFileBuffer(path)`**：有快取；本分頁內的編輯與 agent 回合結束
後的重整會反映進來，但**別人在另一個瀏覽器改的不會自動推過來**。它回傳的 `entry.status` 是
`"loading" | "ready" | "error"`，三種都要處理——`ready` 時才有 `entry.text`。

其他事情走 `useFileService()`。完整介面（型別 `FileService`，也從 SDK 匯出）：

| 成員 | 做什麼 |
|---|---|
| `readFile(path)` | 讀一個檔，回 `FileContent`（`kind: "text" \| "bytes"`）。**要顯示的話用 `useFileBuffer` 就好**，這個是要自己控時機時用 |
| `listFiles(prefix?)` | 列檔案，回 `FileInfo[]`。`prefix` 會下推到後端，**別列整棵樹再自己過濾** |
| `listDirs()` / `listTree()` | 只要資料夾 / 一次遍歷同時拿檔案與資料夾（要兩者時用這個，不要併發呼叫上面兩個） |
| `writeFile(path, body)` | 寫檔（字串／`Blob`／`ArrayBuffer`）。先看 `canWrite`，見下一節 |
| `deleteFile` / `moveFile` / `copyFile` / `mkdir` | 檔案操作，同樣受權限管 |
| `refreshFiles()` | 強制把 sandbox 的變動同步進來再讀。少用——它會走一次後端 |
| `fileUrl(src, fromPath?)` | 把 markdown 的 ref 解析成瀏覽器 URL，`<img src>` 用 |
| `fileDownloadUrl(path)` | 給 `<a download>` 存單一檔案的 URL |
| `prepareDirDownload(prefix)` / `dirDownloadUrl(id, prefix)` | 打包整個資料夾成 zip 再下載，兩段式 |
| `scopeId` | 這個 workspace 的 id。做自己的快取 key 時用它做前綴 |
| `caps` | **這個介面**支不支援某操作（不是權限，見下一節） |

路徑用 workspace 的絕對路徑（開頭 `/`），跟檔案樹看到的一樣。

### 4.3 Entity（可省略）

只有當你的 kind 要畫「entity 紀錄」（issue、milestone 這類結構化紀錄）時才需要。你要在註冊時
宣告 `needsEntity: true`，那份 view 檔就必須寫 `entity:`；沒寫的話使用者會看到一則明確的提示。

**判準是那份 view 檔有沒有寫 `entity:`，不是你有沒有宣告 `needsEntity`。** `needsEntity` 只
決定「沒寫 `entity:` 時要不要擋下來」。所以一份寫了 `entity:` 的檔案，即使你的 kind 沒宣告
`needsEntity`，下面這些 props 一樣會有內容。**view 檔沒寫 `entity:` 時它們才是空的**——那是
正常的，不是壞掉：

`EntityViewProps` 全部欄位（`spec` 見上一節）：

| prop | 是什麼 |
|---|---|
| `entities` | 紀錄陣列。view 檔沒寫 `entity:` ⇒ `[]` |
| `invalid` | 解析失敗、被排除在投影外的紀錄。畫個提示比裝作沒事好 |
| `type` | 該 entity 的 schema（欄位、role、表單）。沒有就是 `null` |
| `users` | 使用者名冊，畫 assignee 之類的 `actor` 欄位用 |
| `refIndex` | 被關聯到的其他型別紀錄索引（例如 issue 的 milestone） |
| `canWrite` | 這位使用者能不能寫。**寫入 UI 一律由它決定顯不顯示**，見下一節 |
| `busy` | 有寫入在飛。用來 disable 按鈕、避免重複送出 |
| `onCreate(args)` | 新增一筆。**要改 entity 一律走這些回呼**，不要自己打 API |
| `onPatch(number, patch)` | 改一筆的欄位 |
| `onPatchAnchor(number, patch)` | 改「被 ref 指到的那個型別」的紀錄（例如從 issue 改它的 milestone）。沒接就是 `undefined` |
| `onOpenRecord(number)` | 在畫面內開紀錄的編輯 modal。沒接 ⇒ 別畫那個入口 |
| `onOpenRecordFile(number)` | 另開分頁到該紀錄的 `.md` 原始檔。同樣可能 `undefined` |
| `viewKey` | 這個 view 的穩定識別（含 item 與檔案路徑）。存「這個人在這個 view 的摺疊狀態」這類 UI 偏好時當 key 用 |

⚠️ 凡是標「沒接就是 `undefined`」的，**要先判斷再畫**——畫一個按了沒反應的按鈕比不畫更糟。

## 5. 沙盒半邊：要算的東西在 item 的沙盒裡算

資料大、或是要 pandas 這類計算時，plugin 帶一個**沙盒半邊**：一個標準的 prebuilt tool bundle，
遵守工具的三段式合約（`launch` → 指令清單、`launch <cmd>` → schema、`launch <cmd> '<json>'` → 執行）。
網頁那邊用 SDK 的 hook 呼叫：

```tsx
import { useSandboxRun } from "@aiws/view-sdk";

const run = useSandboxRun("chart", "query", { source: "data/yield.csv", group_by: "lot" });
// run: { data?: {stdout, stderr, exit_code}, error: Error | null, isLoading, refetch() }
```

- 在**這個 item 的沙盒**裡跑 `../.tools/<plugin>/launch <cmd> <args 的 JSON>`，路由是
  `POST /api/a/{slug}/items/{id}/view-plugins/{plugin}/{cmd}`，body `{"args": {...}}`，權限是 `read_content`。
- `exit_code` 不是 0 是**答案的一部分**（指令說不，看 `stderr`）；`error` 只代表這次呼叫根本沒跑成
  （被拒、沒登入、不在 item workspace 裡）。
- 同一組 `(item, plugin, cmd, args)` 會被快取；要等輸入齊了再跑，傳第四個參數 `{ enabled: false }`。
- **args 走 argv**：`JSON.stringify(args)` 到 128 KiB 會直接被拒（413）——大的輸入（一串值、一張表）
  先寫成 workspace 檔案，傳路徑。
- plugin 的指令**不是 agent 的工具**，不會出現在任何 app 的工具清單或工具選擇器裡。
- 指令名只能是一個簡單的字（`[a-z0-9][a-z0-9_-]*`）；執行時**不帶** item 的環境變數（這條路讀得到
  item 的人都能呼叫，而那些變數是擁有者給自己的工具用的憑證）。
- 同一個 item 的 app 若有同名的第三方工具，`/.tools/<名字>` 是那個工具的，呼叫會被拒絕（409）——
  換個 plugin 名字。

**用隔離的 launcher。** 在 bundle 原始碼的 `pyproject.toml` 宣告：

```toml
[tool.workspace-tool]
launch = "isolated"
```

一般工具 bundle 故意讓使用者 `pip install --upgrade` 的版本優先（#581）；plugin 的指令是平台的、
每次開 view 都會跑，所以隔離版 launcher 用 `python -s`、`PYTHONPATH` 只放 bundle 自己的
site-packages，使用者的 user site 與 `PYTHONPATH` 都進不來。

**bundle 怎麼到沙盒裡**，看沙盒後端：

| `sandbox.kind` | `{"bundle": …}` | `{"artifact": url}` |
|---|---|---|
| `local` | 開機時複製進一個合併的工具根目錄（jail 只 bind-mount 一個根目錄，所以是複製不是連結）；和工具套件撞名 ⇒ 拒絕開機；bundle 資料夾不在 ⇒ 開機印一行、指令逐次報錯；在但沒有可執行的 `launch` ⇒ 拒絕開機 | 不支援（呼叫時明確報錯） |
| `http`（正式環境） | 必須已經在 sandbox-host 的 `builtin/` 裡；不在 ⇒ 呼叫時報錯並點名 plugin | 跟 app 的 `external_tools`（#674）同一條路解析、快取、掛載 |
| `docker` | 不支援（呼叫時明確報錯） | 不支援 |

`http` 的 artifact 和工具一樣是**建立沙盒時才掛載**：plugin 裝好之前就開著的沙盒裡沒有它，呼叫會說明
要等那個環境閒置回收、或開新的。

## 6. `show_file` 前先驗：`validate`

`plugin.json` 的 `sandbox` 寫 `"validate": true`，agent 對你的 kind 的 `*.ai.yaml` 呼叫 `show_file` 時，
平台會**先**在沙盒裡跑：

```
../.tools/<plugin>/launch validate '{"path": "<workspace 相對路徑>"}'
```

- exit 0 ⇒ 檔案照常顯示，stdout 的**第一行**附在 tool 回覆後面（例如
  `highlight matches 3/25 groups; fail_rate 0.02–0.41`）。
- 非 0 ⇒ 回覆錯誤（`stderr`，沒有就 `stdout`），**什麼都不顯示**——跟路徑解析不到時同一條規則。
- 沒宣告 `validate` 時，唯一的檢查是 YAML 能 parse。內建 kind 與其他檔案完全不受影響。
- **驗證跑不起來時照常顯示**，回覆附一句 `(view plugin '<名字>' could not check this view: …)`：
  這個沙盒後端不跑 plugin 指令（`docker`）、app 有同名工具、或沙盒裡沒有這個 plugin 的 launcher。
  那是部署的問題，不是那份 view 檔的，拒絕只會讓 agent 去「修」一份正確的檔案。

## 7. 給 agent 的：skill 與 `## Available views`

- `views` 的每一條會成為 agent prompt 裡 `## Available views` 的一行。
- `skill/` 會成為一個共用 skill。
- 兩者都**依能力發放，不是依清單**：凡是這個 item **解析後**的工具同時有 `write_file` 與 `show_file`
  的，就看得到（改 `app.json` 是改原始碼，而 runtime plugin 就是為了不改原始碼）。使用者仍可在
  skills 面板把某個 plugin 的 skill 關掉。
- 只有 `SKILL.md` 的 skill **永遠即時讀原始檔**，維運方改了下一輪就生效；帶其他檔案的 skill 在第一次
  `read_skill` 時會複製進 workspace，之後那份副本優先，直到使用者在 skills 面板按 Refresh（#589）。

### 對自己的模型調 skill：`view_plugin tune`

觸發率取決於模型，而正式環境跑的是我們看不到的模型——所以 plugin 附情境、由維運方自己調：

```bash
uv run python -m workspace_app.view_plugin tune <name> [--preset P] [--app A --profile B] [--config config.yaml]
```

它對**已安裝的** `<plugin 目錄>/<name>/<plugin.json 的 skill>/SKILL.md` 與 `scenarios/` 跑
`skill_eval --control`，印出報告。流程就是：改那一個檔、重跑。沒有 skill 或沒有情境會直接點名報錯。
本機的 Ollama 模型記得加 `--num-ctx`：Ollama 自己的預設窗口比組好的 prompt 小時會靜默截掉，量到的是窗口不是 skill。
情境格式見 [擴充平台](extending-the-platform.md) 的 skill_eval 一節。

## 8. 工具：`new` / `build` / `check`

```bash
# 產生一個能直接 build 的 plugin（view-plugins/<name>/）
uv run python -m workspace_app.view_plugin new <name> [--with-sandbox] [--with-skill]

# 安裝：web 半邊走 view-plugins/build-web.mjs、sandbox-src/ 強制重新 prebuild，裝完跑 check
uv run python -m workspace_app.view_plugin build view-plugins/<name> .view-plugins
make view-plugins        # = build --all view-plugins .view-plugins

# 不開機檢查已安裝的 plugin
uv run python -m workspace_app.view_plugin check [name] [--config config.yaml]
```

`check` 用開機時同一套規則檢查 manifest 與 skill，另外擋下開機看不出來的事：`web/` 裡**自帶 React**
的建置、**development build**、還讀著 **`process.env`** 的建置（每一條都指出該改哪個設定）；
`"validate": true` 卻沒有 `validate` 指令、或 python 沙盒半邊不是用隔離 launcher 建的，也會擋。
CI 與映像建完 plugin 都會跑它。

原始碼的 `sandbox-src/`（一個 uv 專案，恰好一個 `[project.scripts]`，宣告 `launch = "isolated"`）由
`build` 裝成 `sandbox/`，所以有 `sandbox-src/` 的 plugin，`plugin.json` 要寫 `"sandbox": {"bundle": "sandbox"}`。
app 映像的 `view-plugins` stage 用**同一支** `build-web.mjs` 建每個 plugin 的 web 半邊，裝到
`/app/.view-plugins`——**只有 web 半邊**：正式環境（`sandbox.kind: http`）的沙盒半邊在 sandbox-host；
用這個映像跑 `sandbox.kind: local` 時，帶 `bundle` 的 plugin 沒有 `sandbox/`：開機印一行
`⚠ view plugin <名字>: sandbox.bundle … is not in this plugin dir…` 後照常開機，它的沙盒指令逐次呼叫時
明確報錯（runner 502、`show_file` 附註）。要它能算，自己掛一個用 `view_plugin build` 裝好的 plugin 目錄。

**開發時**：`make view-plugins` 裝到 `<repo>/.view-plugins`（`view_plugins.dir` 沒設時的預設），重啟 app；
`pnpm run dev` 的 dev server 也會輸出 import map（指向 Vite 自己 serve 的 React facade 與 SDK 原始碼；
plugin 經 `/api` proxy 從後端載入——規劃期的 spike 在 dev server 上驗過這條路）。改了 plugin 的 web 原始碼要重跑 build，
改已安裝的 `SKILL.md` 則下一輪就生效。

## 9. 邊界與相容性

**plugin 只從 `@aiws/view-sdk` 進平台。** 它的 web 原始碼可以 import 套件、import 自己資料夾
（`view-plugins/<name>/`）裡的檔案，但不能用相對路徑伸出去——伸進 app 的原始碼等於把 app 的模組
**再編一份**進 plugin（第二個註冊表、第二套 React context）。`web/src/ext/imports.test.ts` 守著。
測試檔（`*.test.tsx`）例外：它們得掛真正的容器與 provider。

**讀寫檔一律經 `FileService`**，不要自己 `fetch`——權限、scope、快取都在那層。

**`caps` 不是權限。**

- `useFileService().caps`：**這個介面**支不支援某個操作（例如 KB 文件頁不能寫）
- props 上的 `canWrite`：**這位使用者**對這個 item 有沒有寫入權

真正的強制在後端。你的寫入按鈕要看 `canWrite` 決定顯不顯示。

**SDK 有版號。** runtime plugin 是對 SDK 的某個**快照**建出來的，所以 `renderers/entity/public.ts`
移除或改動一個匯出，會在執行期弄壞已經建好的 plugin，而不是在編譯期紅。這種改動要升
`SDK_VERSION` 的主版號，舊 plugin 就會在自己的面板上被明確拒絕。這個 repo 裡的 plugin（`view-plugins/*`）
由 `web/` 的 `tsc` 對著真正的 barrel 型別檢查，所以它們仍在編譯期就會紅。

目前還在變動、建議先別依賴的部分：`ViewConfig`（表格／甘特的齒輪面板設定）還在長新欄位。

## 10. 另一條路：編進 SPA 的 `web/src/ext/`

`web/src/ext/` 仍然存在，是**編譯期**的通道：`main.tsx` 在第一次 render 前 `import "./ext"`，
在那裡 `registerViewKind` 的 kind 會隨 SPA 一起 build。這個 repo 本身已經不在那裡註冊任何東西
（`csv-table` 搬成了 runtime plugin）；帶自己 build 的發行版（例如 EE）用它。規則同上，只是入口寫成
`../renderers/entity/public`，而且改動要「開 PR → 合併 → 重新 build → 重新部署」才會生效。

---

## 相關

- [擴充平台：Tools / Skills / Workflows](extending-the-platform.md)——其他擴充面
- [寫一支工具（外部作者）](tool-authoring.md)——工具 bundle 的三段式合約與 #674 artifact
- [設定](configuration.md)——`view_plugins.dir`
- `web/src/renderers/README.md`——檔案預覽 renderer（另一層，目前尚未開放第二方註冊）
