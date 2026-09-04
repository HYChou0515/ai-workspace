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

同一個形狀還在 `apps/subagents.py` 的 `workspace_subagent_defs` 和 `apps/skills.py` 的
`workspace_skill_metas` —— 這兩支是
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

全部從真入口(TestClient + 真路由)進去,鋪 pm profile 真正的 issue / milestone schema,
資料量比照回報的 68 issues + 7 milestones。

**⚠️ 兩張表用的是兩把不同的尺,所以分開放。** 混在一起是 veracity review 抓到的錯:原本的
「熱路徑」表把冷路徑的數字填進 `master 原始` 那一列,而一個 `~` 遮不住那件事 —— 那不是同一
個量測的近似值,是另一個量測。

**(一)冷路徑:facade 操作數**(`_warm` + `ls` + `_read_with`,沒有 live sandbox)

| 端點 | 回傳筆數 | master | Phase 1 | Phase 2 | Phase 4(現況) |
| --- | --- | --- | --- | --- | --- |
| `GET /entities/issue` | 68 | 148 | 81 | 76 | **6** |
| `GET /entities/milestone` | **7** | 164 | 91 | 86 | **9** |
| `GET /entities`(型別目錄) | 2 型別 | 10 | 10 | 5 | **3** |

**(二)熱路徑:sandbox 來回次數**(有 live sandbox = 正式環境的形狀)

| 端點 | master | 快速道路關 | 快速道路開 |
| --- | --- | --- | --- |
| `GET /entities/issue` | 152 | 76 | **8** |
| `GET /entities/milestone` | 168 | 86 | **12** |

⚠️ Phase 3 的 commit 訊息寫的是 `GET milestone 81→8` / `GET issue 71→4`。**那兩組數字是對
的,但標籤是錯的** —— 它們量的是直接呼叫 `EntityStore.query()`,沒有經過路由,所以不含路由
自己的型別目錄探索與 `ls`。走真路由是上表的 86→12 / 76→8。十倍的形狀兩種量法都成立,標籤
不成立。

Phase 2 的 `GET /entities` 10→5 裡有 **4 次**來自一個計劃原本沒寫的改動:`discover_catalog`
不再逐型別問兩次 `exists`,而是從已經拿到的清單推導(第 5 次來自把兩個讀合併)。那是對的做法
(`workspace_skill_metas` 早就這樣做),但它**當初沒寫進計劃就做了** —— review 把它列為未揭露
的範圍擴張,補記於此。

Phase 1 後剩下的 81 次裡有 70 次是「一筆記錄一次檔案抓取」—— 那是 `download` 一次只拿一個
檔案的硬下限,不是解析次數的問題。Phase 3 打破的就是這個下限。

**量法**:把 `WorkspaceFiles.read_many` 從類別上拿掉再重跑,同一個行程內比對前後 —— 這樣兩個
數字才是同一把尺。⚠️ 留在 `$CLAUDE_JOB_DIR/tmp/` 的那支探針**並沒有真的這樣做**:它把
master 的數字寫死成常數來對照。結論經 veracity review 獨立重量後成立(把 `read_many` 藏起來
會精確回到 10 / 148 / 164),但那支探針不能當成證據 —— 它連 master 真的漂移了都看不出來。
探針刻意不進 repo:它是量尺,不是回歸守門員;守門員是下面 Test plan 那些會紅的測試。

## Design

`WorkspaceFiles.read_many(workspace_id, paths)` —— 一個操作解析一次活性,每一步保證打到
同一個 store。這**不是新設計**,是多步驟寫入路徑已經在用的 `_read_with` 契約
(`files/facade.py` 的 `_read_with`),`_parse_type` 只是沒用它。

**它不是「把探測結果快取起來」**,那個做法程式碼裡已經評估過並否決:`_warm` 同時是
sandbox 被 host 回收後的重建觸發點,跨操作記住「還活著」會把復原變成 500
(`files/facade.py` `_warm` 的註解寫得很清楚)。這裡只把範圍收斂到**單一操作內**。

呼叫端用 duck-type 取用,跟 `stat_all` / CAS 那組選配能力一樣
(`filestore/protocol.py` 在 `exists` 下方記載了這個慣例),所以 wiki store 和測試替身不必長出這個方法。

## Locked decisions

**Phase 3 是一條快速道路,不是新介面(使用者,2026-09-04)**:「P3 只是快速道路,不應該影響
interface」「對 caller 應無感」。

這條約束把幾件事一次定死:

- **呼叫端一行都不用改**。`read_all` / `read_all_existing` 的簽章與語意不動;能不能批次是
  在底下偵測的,偵測不到就走原本那條路(mock、local、還沒跟上的 host 都照常運作)。
- **部分失敗不是我要選的東西**。有沒有走快速道路,行為必須一模一樣:`read_all` 照樣丟
  例外,`read_all_existing` 照樣略過缺的那個、其餘照常回。
- **驗收條件因此是「測不出差別」**:同一套測試在快速道路開與關之下都要綠,唯一能觀察到
  差異的地方是操作數。這也順便是一個必紅對照組 —— 如果關掉快速道路測試仍然全綠,那代表
  測試根本沒走到那條路。

## Phases(一階一 commit)

- **Phase 1 — entity 列表**(已完成,PR #781):`_parse_type` 走 `read_many`。
  148→81 / 164→91。
- **Phase 2 — 其餘 workspace 呼叫點**:`entity/catalog.py`、
  `workflow/workspace_store.py`、`apps/subagents.py`、`apps/skills.py` 套用同一個原語。
  這是把同一條規則下沉到所有算這個值的地方 —— 兩套並存保證會漂移。
- **Phase 3 — 批次讀取的快速道路**(已完成):`download_many` 作為**選配能力**加在
  `HttpSandbox`(正式環境,POST `/sandboxes/{id}/files`,base64,缺檔回 `null`)、
  `LocalProcessSandbox`(連帶 `IsolatedProcessSandbox`,一次 thread hop)、以及
  `sandbox-host` 的對應端點。沒有這個能力的後端(mock / docker)照舊逐筆,呼叫端零改動。
  走真路由量:milestone 86→12、issue 76→8 次 sandbox 來回(直接呼叫 `EntityStore.query()`
  則是 81→8 / 71→4 —— 見上面 Measured ground truth 的標籤更正)。
- **Phase 4 — 冷路徑**(已完成):沒有 live sandbox 的 item 是由 durable store 回答的,
  那裡的來回是打資料庫,sandbox 的快速道路對它一點用都沒有。`SpecstarFileStore.read_many`
  用**一次** `path in (...)` 查詢取代逐列取得,同樣是選配能力、同樣呼叫端零改動。
  查詢**同時**用 `workspace_id` 收斂 —— 只比對 path 會把某個 item 的記錄交給另一個
  同名的 item。
  ⚠️ 用的是 `_ls_sync` 已經在下推的**同一個** `path` 索引,所以沒有新增 migration 風險:
  一個答不了 `path` 條件的 workspace,`ls` 本來就已經讀成空的 —— 那正是 `/api/readyz`
  擋著 rollout 的原因。

  **specstar 的公開 API 沒有整批取得完整資源的方法**(`get_many` 只在 meta store 上,
  `read_metas_bulk` 在 `SimpleStorage` 上,兩者都是內部)。所以這裡走的是公開的
  `list_resources` + `QB[...].in_()`,而不是伸手進內部 —— 若之後想要更省,該做的是去
  specstar 開 Discussion,不是在這邊繞過它。

## 刻意不做的

- **不加衍生索引 / 快取表**。entity 是刻意「file-first、讀的時候才算」——
  `entity/store.py` 的 module docstring:`get`/`query` scan-and-parse (no index — §S2)。
  加一份衍生狀態就要處理失效與回填,而且會靜默錯。加速要靠減少來回,不是複製一份狀態。
  (⚠️ 這裡原本引的是 `_corpus` 註解裡的「there is no derived index」—— **那串字整個 repo
  都不存在**,是我寫計劃時捏造的引文。想法本身有出處,句子沒有。)
- **不把 `asyncio.gather` 當主要解法**。它降的是牆鐘時間、不是操作數,而且在 hosted
  sandbox 上一次噴 68 個併發請求,很可能只是把排隊從一個地方搬到另一個地方。Phase 3
  做完之後如果還需要,再單獨談。

## 待解問題

1. ~~**批次讀取的部分失敗語意**~~ —— 由上面的 locked decision 定了:語意不變,所以批次
   原語必須能分辨「這個檔案沒有」和「整批失敗」,兩種既有行為才都保得住。
2. ~~**批次大小上限**~~ —— 已做:`WorkspaceFiles._BATCH_PATHS = 200`,而且**兩條路共用同一個
   分塊迴圈**(`_batch_lane` 把兩者包成同一個形狀)。刻意**不做成 config 旋鈕**(新旋鈕要記
   migrations 帳,而這是純內部細節)。
   ⚠️ review 抓到第一版只有 sandbox 那條有分塊,冷路徑把整串路徑塞進一句 SQL `IN` ——
   跟 `kb/graph/link.py` 的 `PAGE = 500` 同一類問題(4 萬個 id 組出 937KB 的 statement 被
   資料庫拒絕)。**加第二個批次原語時沒沿用已經在樹上的規則**,這是這輪最嚴重的一個。
   ⚠️ 上限是**筆數**不是位元組。200 個大檔仍可能組出很大的一包;要用位元組當上限得先知道
   大小,那又是一趟往返。目前接受這個取捨,但它是已知的邊界。
3. **host 契約**。`sandbox-host` 跟 API 同一條 CI/CD,所以「要重推 host」不是風險,也**不該
   為版本歪斜設計降級路徑**。
   ⚠️ 要講清楚退化的**邊界**:偵測是看後端有沒有這個能力(mock / docker 沒有 ⇒ 逐筆),
   這擋得住「後端不支援」,擋不住「host 舊版但 client 新版」—— 那會是 404,不是靜默變慢。
   依上面那條規則,這裡刻意不做 skew 的降級路徑;若哪天前提不成立,要改的是部署流程,不是
   在 client 埋一條沒人測得到的回退路徑。

## Verified ground truth(檔案指標)

- 每次操作都探活:`files/facade.py` 的 `_warm`,註解說明為何刻意不快取
- 已解析活性的讀取接縫:`files/facade.py` 的 `_read_with`
- entity 逐筆讀:`entity/store.py` 的 `_parse_type`(Phase 1 前是逐筆 `await ... read`)
- rollup 逼出跨型別 corpus:`entity/store.py` 的 `_corpus` 與 `query`
- PM 只有 issue / milestone 兩個型別:`apps/pm/profiles/default/.entity/`
- 選配能力 duck-type 的慣例:`filestore/protocol.py`(`exists` 下方的註解)
- 既有的活性探測計數慣例:`tests/files/test_quota.py`(`_WalkCountingSandbox`)

## Test plan(紅燈先行,只跑 targeted)

Phase 1 的紅燈測試斷言**行為**而不是實作:「解析 workspace 在哪的成本不隨記錄筆數成長」
(`tests/entity/test_entity_store.py`)。修改前 3 筆 4 次、30 筆 31 次(線性);修改後兩者
相同。量法沿用 `tests/files/test_quota.py` `_WalkCountingSandbox` 的**做法**(在 sandbox
邊界數 `exists(handle, "/")`),不是新發明的判準;類別本身是新的
(`tests/warm_workspace.py` 的 `ProbeCountingSandbox`),因為要跨測試檔共用。

Phase 2 每個呼叫點各一條同形狀的測試(成本不隨 workflow 數 / 型別數成長)。
