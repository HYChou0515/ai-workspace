# Plan — #419 PM app + `entity`(file-first 結構化記錄)框架擴充

> 狀態:P1–P4 + FE 已合併(PR #423)。剩項於 P5–P10 補齊(見文末 follow-up phases)。flat integer phases(P1、P2…)。
> 背景:現有 app(RCA / topic hub / playground)的 item 內都是自由內容(chat + 檔案),
> **沒有「一筆筆帶 schema、可篩選、可畫成表/圖」的結構化記錄**。#419 引入 `entity` 層,
> 並以 PM app(管 issue / milestone / gantt / 報表)當第一個消費者。

## 核心概念

- **層級 `app → item → entity`**。item = 一個場所(PM 的 project、RCA 的 investigation);
  **entity = item 內、被系統 parse frontmatter、可投影成 view 的結構化記錄**(`issues/5.md`、`milestones/3.md`)。
- **entity 是 opt-in 附加層**:**沒宣告 schema 的 app 行為完全不變,感覺不到 entity 機制存在;不得滲進 item 的基本行為。**
- **檔案即 entity**:frontmatter(結構化欄位,系統 parse)+ body(自由書寫,直接顯示);
  GitHub 式編號 `N` 為永久 id、單調遞增、不回收、不 rename;ref 指 number 不指路徑。

## 鎖定決策(grill-me)

### 儲存 / 索引(§ 地基)

| # | 主題 | 定案 |
|---|------|------|
| S1 | entity 住哪 | **一般 workspace 檔**,活在既有 sandbox filesystem;真相源 = 平台既有的耐久檔案庫(mirror→FileStore→restore)。**entity 程式只做 sandbox FS 操作,不呼叫 FileStore API**;durability 是平台順手做的。 |
| S2 | 索引 | **不建索引。** view 直接掃 entity 檔投影(frontmatter 活在 blob 裡,沒有 metadata-only 捷徑;與 #395/#411 的「明明只要 metadata 卻抓全 blob」不同)。 |
| S3 | 誰渲染 view | **現在前端做(讀檔畫)**;複雜的將來後端算,**保留**分化空間(簡單前端 / 複雜後端)。 |

### 配號器(§C 的核心)

| # | 主題 | 定案 |
|---|------|------|
| N1 | 撞號 | **不能撞。** 用 **exclusive-create 搶格子**(entity 檔本身即憑證):`exclusive-create issues/N.md`,POSIX 保證同名只有一人建成,EEXIST → 重算重試。**非** flock、**非** 計數器覆寫(覆寫會 lost-update)。 |
| N2 | 永不回填 | user 有 terminal / 檔案工具、**擋不住他自己 `rm`**;「軟刪」無法強制,故**允許硬刪**。永不回填靠一個「最高水位」計數器:`N = max(seq, 現有最大號)+1`,**只前進不後退、self-heal**。 |
| N3 | 計數器放哪 | 一個檔,住 **`root/.readonly/`**(工作區內 → 被 mirror → durable、活過 reap;放工作區外的 infra 區會被 reap 清掉)。 |
| N4 | 誰能動計數器 | `.readonly/` **root-owned**(有 uid 隔離的正式模式:item uid 不可寫,shell 連 `rm` 都失敗)+ file-tool **path-guard**(dev / `uv-run` 無 uid 隔離時的第二層)+ 不進 filetree。正常操作全擋得住;唯一邊角是 terminal `rm -rf` 整個 dir(自己砸自己腳),被 `max()` floor 自癒。 |
| N5 | 跨 pod / CAS | **不需要。** 已保證同一 item 的 sandbox 只在單 pod 跑、用 pod 本機磁碟 → 單一 FS 就是序列化點,exclusive-create 天生原子。**零 specstar、零 flock、零 CAS 檔。** |

### schema / role(§A)

| # | 主題 | 定案 |
|---|------|------|
| A1 | schema 載體 | **宣告式檔(非 Python model)**。`role` 是封閉詞彙,一個 role **同時決定四件事**:frontmatter parse/驗證、quick-create widget、自動生成工具的參數型別、view 能綁哪個視覺鍵。 |
| A2 | role 詞彙 | text / status / actor / date / daterange / progress / ref / rank / backref / rollup。**表達力上限 = role 詞彙上限**(不給運算式、多步 join、OR/巢狀)。 |
| A3 | forward `ref` traversal | **多跳,但只准穿 to-one `ref`**(永不穿 backref、唯讀、結尾必須純量)。允許 `issue→milestone→epic.title`;仍不是查詢語言(無 filter / fan-out / 中途聚合)。 |
| A4 | backref / rollup | **compute-on-read**:掃引用方檔案當場算,不存、不落地。因 view render 本來就要掃該 type 全部檔,backref/rollup 是**同一趟順手算**,與「不建索引」天生共存。 |
| A5 | rollup 表達力 | `agg` 限封閉集 `{count, sum, avg, min, max}`;`over` 只能是**一個** backref;`where` 只允許**單一欄位 == 值**(不給 AND/OR、巢狀、比較運算子)。 |
| A6 | records 位置 | 在 `schema.yaml` **明寫 `path:`**(不猜複數化、i18n 不卡)。 |

### view(§B)

| # | 主題 | 定案 |
|---|------|------|
| B1 | view 載體 | 宣告檔 `*.ai.yaml`,鍵**綁 role 不綁座標**;系統只 load + 對「view-kind 契約」驗證。 |
| B2 | renderer | **table / gantt / board 三種第一批全做**(chart / dashboard 後續)。 |
| B3 | 可編輯 | view **不是唯讀投影,是可直接操作的編輯面**:拖卡→`update_X(status)`、拉長條→`update_X(span)`、改格→`update_X(field)`,全復用同一套 `update_X`。 |
| B4 | overlay | 兩層:template 出貨 / instance 自訂,**同名覆蓋**(對 schema / skeleton / views 都適用)。 |
| B5 | 可發現性 | view **不能埋深讓 user 找檔點**。放**頂層 `views/`**(每檔宣告 `entity:`),並**登記成主畫面導覽**:app.json `layout`(復用既有)宣告哪些 view 是主螢幕 + 順序 + 名稱 → UI 渲成 Board / Gantt / Roadmap tab;PM app 的 `primary_surface` = views(非 `ide`)。schema / skeleton 收進 `.entity/<type>/`。 |

### 寫入路徑 / 自動生成工具(§C)

| # | 主題 | 定案 |
|---|------|------|
| C1 | 優先序 | **確定 UI(主) > AI(備援) > hand-edit(hardcore 逃生口)**。多數人靠確定 UI 把事做完;UI 表達不了才叫 AI;只有 hardcore 才手改,改壞了就是「那筆他自己不能用」(§E 降級)。 |
| C2 | 「單一寫入路徑」準確版 | file-first 下**無法**逼所有寫入過工具。真正保證 = **UI + AI 兩條都走 `create_X`/`update_X`(單一配號+驗證管線);hand-edit 是被祝福的平行例外,靠讀取端 lint + 配號 floor 自癒**。 |
| C3 | 四工具 | schema 一宣告 → 框架**自動生成** `create_X` / `update_X` / `query_X` / `link_X`;**換 schema 即換整套**,owner 不手寫。 |
| C4 | 工具身分 | 這四個是 **host / 框架程式**(非 user-space sandbox 工具)—— create 要配號、要動 root-owned `.readonly`,需框架特權;它們**操作** sandbox FS,但**跑在框架身分**。 |
| C5 | `query_X` | 讀取路徑 = **掃該 item 的 entity 檔 + parse** + 一組**封閉 filter 詞彙**(role 驅動的相等/範圍,比照 rollup `where`,不給查詢語言),bounded 在該 item。 |
| C6 | 並發 | **樂觀版本檢查**(讀出帶版本/mtime、寫回比對,不符要求重讀);單 item = 單 sandbox = 單序列化點,不追 CRDT。 |
| C7 | 驗證哲學 | **lint but not block**:ref 指到不存在、actor 不在名單 → warning,不擋寫入。驗證在**讀取端**統一(compute-on-read),不在寫入端。 |

### skeleton / quick-create(§D)

| # | 主題 | 定案 |
|---|------|------|
| D1 | 骨架 | 每型一個帶佔位的 md;佔位為封閉詞彙 `{{number}}` / `{{arg.x}}` / `{{arg.x?}}`(選填,缺則省略) / `{{now}}` / `{{actor}}`。**只有變數替換 + 選填省略,無條件/迴圈/運算式**。 |
| D2 | quick-create 表單 | = 骨架裡**帶 `{{arg}}` 的欄位**渲染成 UI(role→widget);無 `{{arg}}` 的欄位不進表單。 |
| D3 | 三路徑收斂 | quick-create / 檔案編輯器 / AI **收斂到同一個 `create_X`**、產出同格式檔。 |

### 容錯(§E)/ 協作(§F)

| # | 主題 | 定案 |
|---|------|------|
| E1 | parser 契約 | 固定回傳「**可渲染結果 + 一串 diagnostics**」。 |
| E2 | 分層降級 | entity frontmatter 壞 → 整檔當 body 顯示、不進投影;view 壞 → 只掛那一面板;schema 壞 → 那個 entity type 降成**無 schema 模式**。任一檔壞**不弄死 app**。 |
| E3 | 健康度 | diagnostics 匯成一個「**專案健康度**」view(本身也是一個 view)。 |
| F1 | 權限 | 復用**既有 item 層級成員**:成員可讀寫、非成員唯讀,**無 per-field ACL**。 |
| F2 | member registry | 復用既有 `UserDirectory`(供 actor role 的值、指派、@提及)。 |
| F3 | 活動流 | 檔案變更事件聚成 feed,復用既有 SSE。 |

### 範圍

- **只做單一 item 內的 entity 世界**;跨 item(roadmap 的「我的工作」跨 project、跨 pod 匯總)**先擱置** → roadmap。

## 重用的既有積木(不重造)

- **App 發現與 manifest** — `apps/catalog.py`(scan `apps/<slug>/`)、`apps/registry.py`(`add_model`)、
  `apps/manifest.py`(`AppManifest` / `layout` / `primary_surface` / `default_tabs`)。
  **擴充**:掃 `.entity/<type>/` + 頂層 `views/`;`layout` 增「主螢幕 view 清單」。
- **field schema → widget** — `apps/schema.py:project_fields`(enum→select、list→tags)是 role→widget 的先例;
  entity 的 role→widget 沿用同思路,但走宣告式 role 而非 msgspec 型別。
- **FileStore** — `filestore/protocol.py`、`SpecstarFileStore`;`read_with_etag`/`write_cas`(C6 樂觀版本檢查的接縫)。
  配號**刻意不用** FileStore(見 N1–N5),只用 sandbox FS exclusive-create。
- **Sandbox 隔離 + infra marker** — `sandbox/isolated_process.py`(per-item uid、cgroup);
  `.home`(#393,provision 時 chown 0700)、`.ready`(#366,root/ sibling、unforgeable)是 `.readonly` **root-own** 的直接先例。
- **schema→tool 先例** — `tooling/dispatcher.py`(pydantic→JSON schema + 執行);entity 的四工具是**新的 host-side generator**
  (非 sandbox package,因需框架特權配號),但沿用「schema 是型別+驗證單一真相源」的模式。
- **FE renderer registry** — `web/src/renderers/registry.ts`(副檔名→renderer);
  **新增** `*.ai.yaml` view renderer + table / gantt / board。
- **gantt / board 先例** — `web/src/components/WorkflowTimeline.tsx`(#283 gantt)、`WorkflowProgress.tsx`(#178 board)、
  `web/src/lib/timeline.ts`(純佈局模型)—— 抽成資料驅動的 view renderer。
- **workflow capability** — `workflow/handle.py` + `workflow/capabilities.py`(`ingest_to_collection` / `upsert_context_card` 的
  produce→review→commit 模式);**新增** entity CRUD capability,**呼叫同一份 `create_X`**、接進 `dsl.py` `CAPABILITIES`。
- **member registry** — `users/protocol.py`(`UserDirectory`)、`agent/tools.py`(`lookup_user` / `mention_user`)。
- **配號原子操作先例** — `turn_control/specstar_impl.py`(`TurnEpoch` create-or-increment CAS)是「單調配號」的思路先例
  (我們改用 sandbox-FS exclusive-create,不落 specstar)。

## Phases(flat integer;走 /tdd,一 phase 一 commit)

### Phase 1 — entity 脊椎(一型、一 view,證明 loop 端到端)

證明 file-first entity 的完整寫→讀→畫迴圈,**且不動任何既有 app 行為**。

- **opt-in 護欄**:無 `.entity/` schema dir → app 行為與今天**完全一致**(entity 不滲進 item 基本行為)。
- 框架:掃 `.entity/<type>/{schema.yaml, skeleton.md}` + 頂層 `views/*.ai.yaml`。
- role:**只做純量子集** `text / status / actor / date / progress`(**先不做** ref / backref / rollup / daterange / rank)。
- 配號:`root/.readonly/` root-owned 計數器 + `max(seq, 現有最大號)+1` + exclusive-create 仲裁(單 pod)。
- 自動生成工具:`create_X` / `update_X` / `query_X`(host 框架身分、操作 sandbox FS)。
- skeleton + **quick-create 表單**(從 `{{arg}}` 生,role→widget)。
- **一種 renderer:table**(可改格 → `update_X`),經 app.json `layout` 登記成主畫面。
- 容錯:parser「可渲染 + diagnostics」契約 + entity 層降級。
- 範圍:單 item、單 pod。
- **DoD**:宣告一個 `issue` schema → quick-create 表單 + `create_X` + 主畫面一張可編輯 table。

### Phase 2 — 關聯 + 其餘 renderer

- role 補齊:`daterange`、`ref`(多跳 to-one)、`backref`、`rollup`(封閉 agg + 單一相等 `where`)、`rank`;`link_X` 工具。
- renderer:**board**(拖卡 → `update_X(status)`)+ **gantt**(拉長條 → `update_X(span)`、`deps`、`group_by`)。
- **專案健康度** view(diagnostics 匯總)。

### Phase 3 — workflow capability + 協作

- entity CRUD 成為 **workflow 一級 capability**(**同一份 `create_X` 管線**、同一套配號+驗證;接進 `wf.*` + user DSL;
  **不得** raw `wf.write` 建 entity)。
- 協作:活動流(檔案變更 → SSE)、@提及(`UserDirectory`)、`update` 樂觀版本檢查。
- 權限:復用既有 item 成員(可能免費)。

### Phase 4 — 真正的 PM app

- 出貨 `apps/pm/`:`app.json` + issue / milestone 的 `schema.yaml` / `skeleton.md` + board / gantt / table / roadmap views +
  prompt + picker。**純宣告式 bundle**,疊在 P1–P3 的框架上 —— app owner 體感等同做 RCA。

## 完成剩項的 follow-up phases(P5–P10;P1–P4 已合併 PR #423)

初版交付(PR #423)完成了端到端迴圈與 PM app,但以下 plan 明列項目當時**未做**,於此批補齊(每 phase 一 commit、綠燈才進下一個):

### Phase 5 — agent 端 entity 工具(補 C1/C2/C3 的「AI 備援」寫入路徑)
chat agent 目前只能用通用 `write_file` 直接寫 `issues/N.md`、**繞過**配號+驗證管線。補上 LLM-callable 的 `create_entity` / `update_entity` / `query_entity`(schema-agnostic:吃 `type_name` 參數,不 hardcode slug),全走 `EntityStore`。接進 tool catalog + function-coherence 驗證,PM `app.json` 開通。

### Phase 6 — 樂觀版本檢查(C6)+ `link_entity`(C3)
`update` 讀出帶版本(etag/mtime)、寫回比對不符要求重讀(復用 FileStore `read_with_etag`/`write_cas` 接縫)。`link_entity(type, number, field, target)` = 設 ref 欄(建在 update 管線上)。

### Phase 7 — 專案健康度 view(E3 / B2 的第四種 view kind)
新 view kind `health`:匯總全 type 的 parser diagnostics(含 invalid 記錄)成一面板。後端補 diagnostics 匯總、FE 補 `health` renderer;PM 出貨 `views/health.ai.yaml`。

### Phase 8 — views-first 主畫面(B5 規格)
`AppManifest.layout` 增 `views: list[str]` + `primary_surface` 增 `"views"` 字面量;FE 依 `layout.views` 渲成命名導覽 tab(Board / Gantt / Roadmap)。PM 由 `primary_surface: ide` + `default_tabs` 改為 `primary_surface: views` + `layout.views`。

### Phase 9 — 協作(F1–F3 / P3 後半)
entity 檔變更 → 活動流(復用既有 SSE / activity log);actor role 值 + 指派 + `@` 提及走既有 `UserDirectory`。權限已由 route 的 `require_item`(item 成員)免費涵蓋。

### Phase 10 — exclusive-create 配號(N1 規格)
把「`.readonly` 計數器覆寫 + in-process asyncio lock」換成 N1 定案的 **exclusive-create（O_EXCL 搶 `records/N.md`,EEXIST → 重算重試）** 當防撞仲裁(高水位計數器續管永不回填);需在 FileStore protocol 補 create-exclusive 原語。移除對「單 pod 單 process」的隱性依賴。

## Roadmap(不進這批 phase,需框架新原語)

- **跨 item 索引**:跨 project「我的工作」(跨 instance / 跨 pod 匯總)—— 現架構沒有此軸。
- **時間 / 圖運算引擎**(確定性、不進 agent):關鍵路徑、baseline 落後、到期偵測、工作日曆、資源負載(RCA 可撿到「圖」那半 = 因果鏈)。
- **排程 / 事件觸發**:到期提醒、自動週報、AI 主動性。
- **通知**:watch 訂閱 + 投遞管道。
- **artifact / report pipeline**:view 快照 + agent 敘述 → 版本化輸出檔(通用化 RCA report 版本機制)。
- **headless agent 觸發**:agent 被排程 / 事件叫起(非只在 chat)。
- **Publish 原語**:sandbox 內單一 self-contained HTML 對外開連結(框架級,三 app 都受用)。

## 待後續定案(不擋 P1)

- `.ai.yaml` 副檔名是否再語意化細分。
- 完整 role→view 鍵相容表、parser 輸出契約的精確欄位、`query_X` filter 詞彙的完整集合。
- `.entity/` 是否進一步收進 `.readonly`(更保護 vs 更好覆蓋)。
