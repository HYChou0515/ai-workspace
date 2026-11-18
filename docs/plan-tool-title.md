# 第三方工具的標題與描述

**狀態:** P1–P6 完成(#1026);P7(demo、review、CI)進行中。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master`(`30defb6c`)的程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻。

## 1. 為什麼

一個第三方工具(tool bundle,#674)在畫面上只有 id。工具面板上的「Rca Tools」看起來像標題,其實是註冊時給的
id `rca-tools` 去掉 `-`、首字大寫(`catalog.humanize_tool_label`)〔user〕「rca-tools 看起來就是id(實際上他也是
註冊時給的id) 在工具面板上 是前端無腦解析去-並留大寫」〔查證〕。

整條路上沒有「標題」:作者的 pyproject、manifest、host 的回應、`PackageInfo`、`/a/…/tools` 的回應、AI 看到的工具、
MCP 的 `tools/list` 都沒有〔查證〕。描述有——`[project].description` 一路傳到 `PackageInfo.description`(#697)——
但工具面板把一個工具拆成「每個指令一列」,工具那一層的標題列只畫 `package`(轉換過的 id),描述沒畫出來〔查證〕。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | **標題寫在作者的 pyproject**:`[tool.ai-workspace] title = "…"`。PEP 621 沒有標題欄位,`[tool.<名稱>]` 是 PEP 518 留給工具自己用的表 | 〔user〕「寫在pyproject」 |
| D2 | **描述沿用 `[project].description`**——作者已經寫過的直接出現,不加第二個描述欄位 | 〔user〕(接受提案) |
| D3 | 只做**工具這一層**的標題;指令那一層維持「名稱 + 描述第一句」 | 〔user〕(接受提案) |
| D4 | 沒寫標題的工具照舊用 id 轉出來的字——舊工具、還沒重新發版的工具都一樣 | 〔施工〕 |
| D5 | `title` 寫了就要是非空字串,否則 build 失敗並說是哪個欄位(和 `env.json` 一樣:寫錯的東西不能被當成沒寫,安靜地發出去) | 〔施工〕 |
| D6 | manifest 多一個選填的 `title`,排在 `description` 後面,同樣只給人看、不決定任何事;**不升 `format_version`**——舊的 artifact 沒有它照樣解析,舊平台看到它也只是略過(`parse_manifest` 只讀它認得的 key) | 〔施工〕〔查證〕 |
| D7 | 平台上「工具叫什麼」只有一個函式決定(`標題 or 轉換過的 id`),工具面板、環境變數面板(兩者都讀 `package`)、整包授權那一列都從它來 | 〔施工〕 |
| D8 | 工具面板的工具標題列照 Material 3 list item 的 headline + supporting text 畫:標題一行;描述在下面,字小一級、顏色淡一級,最多兩行、超過用省略號,完整文字在滑鼠提示裡。滑鼠提示照舊帶 id(認得 id 的人還找得到) | 〔施工〕,引用見 §4 |
| D9 | AI 讀到的工具說明帶上標題:``From the 「<title>」 tool bundle (`<id>`) …``;沒標題時照舊 | 〔施工〕 |
| D10 | MCP:工程師自己的 Claude Code 等接上時,`initialize` 回的 `serverInfo` 帶這個工具的名稱、版本,`instructions` 帶描述(`2024-11-05` 就有);`title` 只在協商成 `2025-06-18` 時帶(那一版才有)。照規格協商版本:客戶端要的版本我們支援就回它,否則回不比它新的裡面最新的那一個,都比它新才回我們最新的(見 C2) | 〔施工〕 |

## 3. 不做

- 指令層的標題(D3)。
- `app.json` 登記時另給標題(user 選了 pyproject)。
- 第一方工具(repo 內建的 package)的標題:它們沒有 manifest,這次不碰。

## 4. UI 依據

Material 3 List(`material-components/material-web` 官方文件 `docs/components/list.md` 與 token
`tokens/versions/v0_192/_md-comp-list.scss`):list item 的文字有 `headline`、`supporting-text`(還有這裡用不到的
`trailing-supporting-text`)幾格;headline(token 叫 `list-item-label-text`)是 `body-large` / `on-surface`,
supporting text 是 `body-medium` / `on-surface-variant`;item 高度有一行、兩行、三行三種(56 / 72 / 88px)。文件沒有直接
規定行數上限,「輔助文字最多兩行」是從最高的三行 item(標題一行 + 輔助兩行)推出來的。m3.material.io 本身要跑
JavaScript 才有內容,讀不到,改讀它的元件庫。

## 5. 經過的每一站

| 站 | 檔案 | 改什麼 |
|---|---|---|
| 作者 pyproject → `Project` | `tooling/builder.py` `read_project` | 讀 `[tool.ai-workspace].title`(D1、D5) |
| manifest | `tooling/artifact.py` + `sandbox-host/…/artifact.py`(兩份逐字相同,`test_artifact` 守著) | `Manifest.title`、`parse_manifest`、序列化(D6) |
| bundle | `tooling/builder.py` | 把標題/描述寫進 bundle 給 `mcp_server.py` 讀(D10) |
| host | `sandbox-host/…/tool_resolve.py`、`app.py` | `ResolvedTool.title`、記住/斷線時回退、resolve 回應 |
| 平台 | `tooling/external.py`、`tooling/registry.py` | `PackageInfo.title` |
| 名字與描述 | `tooling/catalog.py`、`api/tools_routes.py` | D7;每列帶工具的描述 |
| AI | `tooling/registry.py` `describe_command` | D9 |
| 前端 | `web/src/lib/toolGroups.ts`、`components/ToolsChecklist.tsx`、`api/types.ts` | D8 |
| MCP | `tooling/mcp_server.py` | D10 |
| 文件 | `docs/tool-authoring.md`、`docs/migrations.md` | 作者怎麼寫;營運方要做什麼 |

## 6. 階段

| 階段 | 內容 | 驗收 |
|---|---|---|
| P1 | builder 讀標題、manifest 帶標題(兩份 `artifact.py`);CI log 那一行印出標題 | 寫了的進 manifest;沒寫的沒有這個 key;寫錯的 build 失敗並說欄位;舊 manifest 照樣解析 |
| P2 | host 帶標題(解析、記住、回退、回應) | 回退時標題還在;舊的記錄沒有標題照樣能回退 |
| P3 | 平台:`PackageInfo.title`、D7、每列帶工具描述、D9 | 有標題的工具在 `/a/…/tools` 的 `package` 是標題;沒有的照舊 |
| P4 | 前端工具標題列兩行(D8) | 截圖對照 §4;寬窄兩種寬度 |
| P5 | MCP `serverInfo` / `instructions`、版本協商(D10) | 新舊兩種協定版本的客戶端都接得上 |
| P6 | 文件:`tool-authoring.md`、`migrations.md` | `tests/docs`、`mkdocs --strict` |
| P7 | web-demo → review(conformance / veracity / defect / regression)→ CI | 照 CLAUDE.md |

## 7. 施工與審查中定的細節

- **C1** 只有一個指令的工具在面板上沒有標題列(那一列就是整個工具),描述畫在那一列的名稱下面,和標題列用同一個
  輔助文字元件;收在組裡的列不畫,標題列已經有。那一列的滑鼠提示加上 id(`rca-tools:spc · 標題 · 指令`)。
  原本只做了標題列,只有一個指令的工具就哪裡都看不到描述——把需求做窄了(review 第 1 輪)〔施工〕。
- **C2** 版本協商:客戶端要的版本我們不支援時,回「不比它新的裡面最新的」。要 `2025-03-26` 的客戶端不認得
  `2025-06-18`,可能直接斷線;`2024-11-05` 是它以前就接受的。客戶端沒寫版本(規格說一定要寫)就回 `2024-11-05`——這個 server 以前對誰都回這個(review 第 1 輪)〔施工〕。
- **C3** `instructions` 在 `2024-11-05` 就有(`schema/2024-11-05/schema.ts`)。原本寫成 `2025-03-26` 才有,
  是查規格的 agent 給的錯答案、沒有自己核對就寫進程式,讓 `2024-11-05` 的客戶端拿不到描述(review 第 1 輪)〔查證〕。
- **C4** 標題要是一行;`[tool]` 不是表也算寫錯——兩者都讓 build 失敗並說欄位,不是噴 traceback(review 第 1 輪)〔施工〕。
