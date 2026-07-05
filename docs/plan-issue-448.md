# Plan — Issue #448：PM app 前端(三 renderer 互動 + 寫入 / 新增 / 容錯 / 協作 / 健康度)

> 本文件是 `/grill-me` 的定案(全程繁中)。#448 是一個 **epic**;本 branch 只交付 **P1 = 基建 + 核心可操作**,其餘拆成 **6 個可平行的 follow-up**。

## 背景與 delta

後端 entity 框架(#419)已完成並合併,前端只落地了 **最小骨架**:
`web/src/renderers/entity/{AiYamlRenderer,EntityViews}.tsx`(table inline 編輯、board status 欄 + select 換欄、gantt 唯讀長條、基本 health)、`api/entities.ts`、`hooks/useEntities.ts`。

**後端已定、前端必須對齊的事實**(不可自立一套):

- **role 封閉詞彙**:`text / status / actor / date / daterange / progress / rank / ref / backref / rollup`;rollup 聚合 `count/sum/avg/min/max`。role 決定 widget、view 綁定鍵、寫入 arg 型別。
- **ref 路徑 traversal 是 renderer 職責**(`projection.py` 明載:`milestone.title` 由前端在 render 時跟 ref number 解析,後端不做、不建索引)。
- **backref / rollup 是 compute-on-read**:前端拿到算好的值,唯讀。
- **寫入只有一條路** `update_X`(含 UI 編輯);entity **number 是永久 id、ref 指 number 不指路徑**。
- **樂觀鎖**:`update` 帶 `expected_version`(= content sha256[:16]);衝突 → 後端 **HTTP 409**,body 目前**只有 message 字串**(不含對方 record),routes **沒有單筆 GET**(只有 list)。
- **容錯契約**:任何 parse/load 回傳 `(result, list[Diagnostic])`;`Diagnostic{level: "warning"|"error", message, field?}`,warning=可用仍 lint、error=從投影中剔除。
- **沒有 batch endpoint、沒有 `failures[]`、沒有 link route**(`link_entity` 就是 `update`)。

**現況缺口**:前端 `update` 沒帶 `expected_version` → 實際 last-write-wins;無 ref-traversal(milestone 顯示 raw number);無排序/篩選/欄顯隱;無衝突 UI;無多選批次;gantt 唯讀;board 只 group by status;無完整 role widget(ref/actor/daterange 用簡易 input)。

## grill 定案

1. **交付範圍**:epic 化。本 branch 只交 P1(基建 + 核心可操作 table + B1/B2 寫入 + D 容錯);A2/A3/批次/C2/C3/E/F 全部 follow-up。
2. **B2 衝突 UX**:**前端重載對齊**(零後端改動)。`useEntityWrite` 帶 `expected_version`;收 409 → 不覆蓋、invalidate list 讓該列顯示對方最新值、跳非阻斷橫幅「這筆已被他人更新」+「重試(用新版本重送)/放棄」。「看對方版本」= 重載後該列即為對方版本。
3. **A1 table 切面**:排序 / 篩選 / 欄顯隱 + ref-traversal 顯示 + **完整 role→widget**(status 下拉 / actor `UserPicker` / date / daterange 起訖 / ref picker 顯 `#N 標題` / progress 條;backref·rollup 唯讀)。**多選批次留 follow-up**。
4. **D 容錯**:**三層降級全做** + Diagnostic 標到欄/列。entity 壞→列/格降級(frontmatter 整段壞→純 body、不進投影、紅標);view 壞→只該面板降級 + 顯示 view diagnostics;schema 壞→該 type 降成無-schema 檔案列表。warning=黃標(有 field 標到該欄)、error=紅標(說明為何未進投影)。
5. **follow-up 拆法**:**6 個全平行**,一 issue 一檔域,靠 P1 兩個接縫解耦。**特別要求:issue 之間碰不到同檔,可同時開工。**

## 平行的兩個接縫(P1 必做,是所有 follow-up 能平行的前提)

1. **view-kind→元件註冊機制**:把 `EntityViewBody` 的 table/board/gantt/health **拆成獨立檔** `renderers/entity/{TableView,BoardView,GanttView,HealthView}.tsx`,由一份 `viewKindRegistry` 分派。→ A2 gantt、A3 board、table 批次各改各的檔,零衝突。新增 renderer(未來 chart/dashboard)= 註冊一個元件 + 宣告吃哪些 role 鍵,不動核心。
2. **`useEntityWrite` 單一寫入 hook**(唯一寫入接縫,每個 renderer 都走它):樂觀更新 + `expected_version` + 409 衝突橫幅 + **`canWrite` 旗標**(P1 預設 `true`;E 之後從權限灌入)+ **SSE-invalidate 訂閱點**(P1 預設 off;C3/E 之後開)。→ E 的唯讀 gate 與即時同步只要「中央翻旗標」,不必逐一改 gantt/board/table 的寫入點。

## 本 branch 的 phase(flat integer,commit per phase,FE-TDD / vitest)

| Phase | 內容 | 主要新增/改動檔 |
|---|---|---|
| **P1** | view-kind→元件註冊 + renderer 拆檔(純重構,行為不變、既有測試綠) | `renderers/entity/{registry,TableView,BoardView,GanttView,HealthView}.tsx`;`EntityViews.tsx` 收斂為 re-export |
| **P2** | `useEntityWrite` 單一寫入 hook(樂觀 + `expected_version` + 409 衝突橫幅 + `canWrite` + SSE seam) | `hooks/useEntityWrite.ts`;`api/entities.ts`(`update` 帶 `expected_version`);`AiYamlRenderer.tsx` 接線 |
| **P3** | role→widget 單一表 + 接進 table inline / board / quick-create(全走 P2 hook) | `renderers/entity/roleWidget.tsx`;`TableView/BoardView` inline;`QuickCreate` |
| **P4** | ref-traversal helper:顯示 `milestone.title`(載 referenced types records、number→record 索引、dangling ref 降級標記)+ ref picker 編輯 | `renderers/entity/refTraversal.ts`;`hooks/useEntities.ts`(多型別載入) |
| **P5** | table 排序 / 篩選(status·actor·date 值域)/ 欄顯隱(本地 ephemeral) | `TableView.tsx` |
| **P6** | D 三層降級 + Diagnostic 黃/紅標到欄/列 | 各 renderer + `AiYamlRenderer.tsx`;`api/entities.ts`(catalog/list diagnostics 型別) |

順序理由:拆檔(解耦前提)→ 寫入 seam(P3+ 都接它)→ widget → ref → table 操作 → 容錯。

## 平行 backlog(P1 交付後可同時開工;檔域互斥)

| # | Issue | 檔域 | 摘要 |
|---|---|---|---|
| 1 | **A2 gantt 互動** | `GanttView.tsx` | 拖長條改起訖(→ daterange `update`)/ 時間軸縮放(日·週·月)/ `group_by` 泳道。**相依線切為子項**(見後端 gap)。 |
| 2 | **A3 board 互動** | `BoardView.tsx` | 拖卡換欄(→ status `update`)/ 卡片面挑 role 欄(actor 頭像·date·progress 條)/ 空欄與超值域 status(lint warning)仍顯示。 |
| 3 | **table 多選批次** | `TableView.tsx` | 多選 + 批次改 status/actor(N 筆 PUT 扇出、逐列 409 聚合、部分成功/部分衝突逐列標示)。 |
| 4 | **C2 單一 entity 檔案編輯器** | 新檔 | frontmatter 表單模式(P3 role widget)/ 原始 YAML 兩檢視 + body 自由書寫;存檔走 `useEntityWrite`;複用 Monaco IDE stack。 |
| 5 | **F 健康度深化** | `HealthView.tsx` + 新面板 | 依 level / entity type / 欄位篩選;點一則跳到出問題的 entity/view。 |
| 6 | **E 即時同步 + 協作** | 新檔 + 中央翻 P2 兩旗標 | C3 AI 建立/更新即時反映(SSE→entity invalidate)/ 活動流 feed(file-first 副產品)/ @提及(member registry)/ **非成員唯讀 gate**(翻 `canWrite`,隱藏所有寫入入口)/ 線上成員 presence。 |

## ⚠️ 標記的後端 gap(寫進計畫,非 P1 阻礙)

- **A2 相依線需 to-many ref**:一個 issue 可依賴多個,但後端 role 詞彙只有 to-one `ref`(traversal 單跳)、現 pm issue schema 也沒有 `deps` 欄。→ 相依線切為 **A2 子項**,前置 = 後端補「多 dep 欄 / list-ref」;A2 其餘(拖曳/縮放/泳道)不受阻。
- **E 唯讀 gate 的權限來源**:接 #303-310 的權限訊號 / item 成員資格。實際來源留 E 開工時 grill。

## 明確不在本 epic(對齊後端 roadmap)

chart / dashboard renderer、跨 item「我的工作」、時間/圖運算引擎前端(關鍵路徑·baseline 落後)、通知投遞 UI、artifact/report pipeline、Publish 原語前端。

---

**關聯**:#419(entity 後端,已完成)。前端一切寫入對齊「單一寫入路徑」與樂觀鎖衝突語意,不另開通道。
