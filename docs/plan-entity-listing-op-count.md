# Plan — 列表變慢的真正原因是「一次請求做了幾百次檔案操作」

## Problem

使用者回報:7 個 milestone / 68 個 issue 的 PM item,取 milestone 要等 45 秒、
entities 15 秒、workflow 10 秒,改完 span 之後 issue 58 秒。這個資料量不該有這種數字。

查下來,**這三個症狀是同一個缺陷長在三個地方**:

| 使用者看到的 | 程式位置 | 形狀 |
| --- | --- | --- |
| milestone 45s | `entity/store.py` `_parse_type` | `ls` 之後逐筆 `read` |
| entities 15s | `entity/catalog.py` `discover_catalog` | 每個型別讀 schema + skeleton |
| workflow 10s | `workflow/workspace_store.py` `workspace_workflow_metas` | `ls` 之後逐筆 `read` |

同一個形狀還在 `apps/subagents.py:154-159` 和 `apps/skills.py:175-189` —— 這兩支是
**每個 turn 都會走**的,不只是使用者點開面板的時候。

關鍵在 `WorkspaceFiles.read`:它每一次都呼叫 `_warm` 重新解析 workspace 的活性,而
`_warm` 對 hosted sandbox 是一次網路來回(`sandbox.exists(handle, "/")`)。所以「讀 N 個
檔案」不是 N 次來回,是 **2N 次**。

`entity` 這一支還有第二層放大:milestone 的 `progress` / `open_count` 是 rollup,一有
rollup 就要建跨型別 corpus(`store.py` `_corpus`),把**每個型別的每一筆記錄全部重讀一遍**。
所以 milestone 只回 7 筆,卻比回 68 筆的 issue 還貴 —— 它慢跟有幾個 milestone 無關。

## 為什麼目標是「操作數」而不是秒數

這個環境量不準時間:F12 的總時間含瀏覽器排隊(同一 host 只有 6 條連線),而
「次數 × 猜的延遲」是循環論證。**操作數是確定的、可重現的、不受網路抖動影響**,而且它就是
延遲的成因。使用者拍板用它當目標。

## Measured ground truth

從真入口(TestClient + 真路由)進去,鋪 pm profile 真正的 issue / milestone schema,
資料量比照回報的 68 issues + 7 milestones。**同一次執行內量前後兩種形狀**(把
`read_many` 藏起來,`_read_all` 就退回舊路徑),所以兩個數字是同一把尺:

| 端點 | 回傳筆數 | Phase 1 前 | Phase 1 後 |
| --- | --- | --- | --- |
| `GET /entities/issue` | 68 | 148 | **81** (−45%) |
| `GET /entities/milestone` | **7** | 164 | **91** (−45%) |
| `GET /entities`(型別目錄) | 2 型別 | 10 | 10(未處理) |

Phase 1 後剩下的 81 次裡有 70 次是「一筆記錄一次檔案抓取」—— 那是 `download` 一次只拿一個
檔案的硬下限,不是解析次數的問題。

探針放在 `$CLAUDE_JOB_DIR/tmp/test_zzz_entity_fanout_probe.py`(刻意不進 repo:它是量尺,
不是回歸守門員;守門員是下面 Test plan 那條會紅的測試)。

## Design

`WorkspaceFiles.read_many(workspace_id, paths)` —— 一個操作解析一次活性,每一步保證打到
同一個 store。這**不是新設計**,是多步驟寫入路徑已經在用的 `_read_with` 契約
(`files/facade.py:300`),`_parse_type` 只是沒用它。

**它不是「把探測結果快取起來」**,那個做法程式碼裡已經評估過並否決:`_warm` 同時是
sandbox 被 host 回收後的重建觸發點,跨操作記住「還活著」會把復原變成 500
(`files/facade.py:281-288` 的註解寫得很清楚)。這裡只把範圍收斂到**單一操作內**。

呼叫端用 duck-type 取用,跟 `stat_all` / CAS 那組選配能力一樣
(`filestore/protocol.py:64-68` 記載了這個慣例),所以 wiki store 和測試替身不必長出這個方法。

## Phases(一階一 commit)

- **Phase 1 — entity 列表**(已完成,PR #781):`_parse_type` 走 `read_many`。
  148→81 / 164→91。
- **Phase 2 — 其餘 workspace 呼叫點**:`entity/catalog.py`、
  `workflow/workspace_store.py`、`apps/subagents.py`、`apps/skills.py` 套用同一個原語。
  這是把同一條規則下沉到所有算這個值的地方 —— 兩套並存保證會漂移。
- **Phase 3 — sandbox 協定加批次讀取**:打破「一筆記錄一次下載」的下限。會動
  `sandbox/protocol.py` + http client + `sandbox-host` + local/isolated/mock 四個實作。
  **動手前必須先 /grill-me**(見下面待解問題)。
- **Phase 4 — 冷路徑**:沒有 live sandbox 的 item,`read_many` 目前仍逐筆打 durable
  store;改用 specstar 既有的批次讀取。

## 刻意不做的

- **不加衍生索引 / 快取表**。entity 是刻意「file-first、讀的時候才算」(`store.py`
  `_corpus` 的註解:there is no derived index)。加一份衍生狀態就要處理失效與回填,而且
  會靜默錯。加速要靠減少來回,不是複製一份狀態。
- **不把 `asyncio.gather` 當主要解法**。它降的是牆鐘時間、不是操作數,而且在 hosted
  sandbox 上一次噴 68 個併發請求,很可能只是把排隊從一個地方搬到另一個地方。Phase 3
  做完之後如果還需要,再單獨談。

## 待解問題(Phase 3 動手前要在 grill 裡拍板)

1. **批次讀取的部分失敗語意**。批次裡有一個檔案讀不到時要回什麼?整批失敗會讓一個壞掉的
   hand-edit 弄垮整個列表(現在的逐筆版本是 skip 掉繼續);逐筆回報則要定義回傳形狀。
2. **批次大小上限**。3000 個檔案的 workspace 不能一次要一整包。
3. **host 契約**。`sandbox-host` 跟 API 同一條 CI/CD,所以「要重推 host」不是風險,也不該
   為版本歪斜設計降級路徑 —— 但要確認這個前提在這次仍然成立。

## Verified ground truth(檔案指標)

- 每次操作都探活:`files/facade.py:256-295`(`_warm`),註解說明為何刻意不快取
- 已解析活性的讀取接縫:`files/facade.py:300-310`(`_read_with`)
- entity 逐筆讀:`entity/store.py:207-215`(Phase 1 前)
- rollup 逼出跨型別 corpus:`entity/store.py:217-232`(`_corpus`)、`248-266`(`query`)
- PM 只有 issue / milestone 兩個型別:`apps/pm/profiles/default/.entity/`
- 選配能力 duck-type 的慣例:`filestore/protocol.py:64-68`
- 既有的活性探測計數慣例:`tests/files/test_quota.py`(`_WalkCountingSandbox`)

## Test plan(紅燈先行,只跑 targeted)

Phase 1 的紅燈測試斷言**行為**而不是實作:「解析 workspace 在哪的成本不隨記錄筆數成長」
(`tests/entity/test_entity_store.py`)。修改前 3 筆 4 次、30 筆 31 次(線性);修改後兩者
相同。量法沿用 `tests/files/test_quota.py` 既有的 `_WalkCountingSandbox`,在 sandbox 邊界
數 `exists(handle, "/")`,不是新發明的量法。

Phase 2 每個呼叫點各一條同形狀的測試(成本不隨 workflow 數 / 型別數成長)。
