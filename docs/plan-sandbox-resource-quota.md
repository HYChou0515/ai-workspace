# Sandbox 資源額度：依 App 設定 + 每人總量

依 **App 種類**設定 sandbox 資源(cpu / memory / disk quota),並限制**一個人跨 App items 總共能用多少**。

本文件是**對帳用的**:每個 phase 都寫「驗收條件」而不是「做了什麼」——條件是「怎麼證明它真的生效」。

相關:issue #687(`owner` 鎖定 + 轉移流程,**本功能具有強制力的前提**)、PR #688(P1+P2)。

---

## 1. 定案的規則

八輪 grill 後逐題拍板的結果。

### 1.1 兩種資源,兩套機制

|  | disk | cpu / memory |
|---|---|---|
| 性質 | **存量** —— 持久,sandbox 死了還在,可加總 | **流量** —— 只在 sandbox 活著時存在,回收後歸零 |
| 機制 | 寫入時檢查,**只擋成長** | 開新 sandbox 時的**准入控制** |
| 帳本 | 對 owner 的工作區加總,需要耐久記錄 | 由「探得到活著」推導 |

「只擋成長」不是效能取捨而是**可用性要求**:縮小、同大小覆寫、刪除永遠放行,否則一個人一旦滿了就再也清不回來。

cpu/mem 的帳**不能**是 `create` 加一、`kill` 減一的計數器:sandbox 會被閒置回收、pod 會猝死、reap 會漏掉,只要漏一次那格額度就永久蒸發,使用者明明零個活著的 sandbox 卻被鎖住。它必須綁活體憑證(心跳 / 租約 / 探活),盤點以「探得到活著」為準——這跟 #366 的 `_alive` 探活、`api/sandbox_activity.py` 的心跳是同一個做法,不是新發明。

### 1.2 債務人:item 的 `owner` 欄位

disk 和 cpu/mem **共用同一條歸屬規則**,不分兩套。item 預設 private,分享出去是 owner 自己的選擇,成本就該是他的;有特殊需求走個人額度擴充,並且**要 review 那個行為**。

> ⚠️ **前提**:`owner` 目前是**沒有任何 checker 守著的普通 Tier-1 字串欄位**,任何有寫入權的人一個 PATCH 就能改成任何人。而且它**不是**權限擁有者(權限走 specstar 的 `created_by`,見 `api/item_routes.py:318`、`perm/scope.py:67`),所以設給別人**不會失去任何控制權**——item 照樣是你的,只有帳單跑到對方頭上。
>
> 因此 **issue #687 是本功能真正具有強制力的前提**。在它落地前,額度從第一天起就是可繞過的。這是知情的取捨(額度先做、鎖定後補),不是疏漏。

### 1.3 撞到上限 = 直接拒絕

不自動幫使用者讓位(不 LRU 回收他自己的 sandbox)。代價是必須配一個**「我的資源使用」畫面**(P8):cpu/mem 可**關閉**、disk 可**刪除**。沒有那個畫面,「直接拒絕」是缺一半的決定——被擋的人不知道要去關什麼。

### 1.4 擋在 turn 送出前

| 入口點 | 程式位置 | 要開新 sandbox 時 | disk |
|---|---|---|---|
| 送出聊天訊息 | `api/chat_send.py:send` | ✅ 擋(使用者訊息存進去之前) | 已有:workspace 滿就拒絕整輪 |
| turn 開頭的預熱 | `api/turns.py:746` | ❌ **不能當閘門** —— 這行是 `suppress(Exception)`,擋了會被吞掉、turn 照跑 | — |
| agent 中途第一次呼叫 exec | `agent/context.py:471` | ⚠️ 這時才擋 = 那一輪 turn 已經燒掉 | — |
| Terminal 面板 | `POST /a/{slug}/items/{item_id}/exec` | ✅ 擋,回 507 | — |
| Workflow 執行 | `api/workflow_exec.py:243` | ✅ 擋 | 已有 `ensure_room_for` |
| 開 item / 瀏覽 / 上傳檔案 | 走 filestore,不碰 sandbox | 不受影響 | 已有 `_ensure_headroom` |

**只有「要開新的」才檢查**:item 已經有活著的 sandbox 就直接放行,那一格他早就佔著了。

排程觸發的 headless workflow 一樣拒絕,但要留**可見的失敗紀錄**——定時任務靜靜沒跑比擋下來更危險。

### 1.5 設定形狀

app.json 宣告胃口,config.yaml 給預設與天花板(k8s 的 Pod requests × namespace LimitRange 分工):

```jsonc
// apps/<slug>/app.json —— 每個欄位都可省略
"resources": { "cpu": 2, "memory": "2G", "disk": "10G" }
```

```yaml
resources:
  per_app:
    default: { cpu: 1, memory: 512M, disk: 20G }
    max:     { cpu: 4, memory: 4G,   disk: 50G }   # 超過 → 開機失敗
  per_user:
    count: 10      # 同時活著的 sandbox
    cpu: 0         # 那些 sandbox 的核心數總和(0 = 不限)
    memory: ""
    disk: 200G     # 名下所有 item 的工作區總和
```

三層解析,**每個維度各自往下掉**:

```
app.json `resources` ◇ resources.per_app.default ◇ sandbox.isolation.* / filestore.workspace_quota
```

最底層是相容性保證:什麼都沒宣告的 App、什麼都沒設定的部署,解析出來就是**今天的數字**。

天花板檢查的是**解析後**的值,所以把過大的數字從 app.json 搬到 `per_app.default` 也躲不掉。超過一律**開機失敗並指名是哪個 App**,不靜默截斷——設定寫 4 cores 而 pod 只給 2,只會在幾個月後變成一個沒人聯想得到設定的效能 bug。

`per_user` 是 k8s `ResourceQuota` 形狀的四個維度,0 = 不限;全站預設 + **可逐人覆寫**。

---

## 2. Phases

### P1 — 宣告與設定 ✅ 已完成(PR #688)

**交付** app.json 的 `resources` 區塊;config 的 `resources.per_app.{default,max}` 與 `resources.per_user`;三層解析;開機天花板。無行為改變。

**驗收**

1. yaml 寫 `resources:` 真的進到 `Settings`(不是只被 whitelist 放行卻沒人建構)
2. 三個既有 App 在預設 config 下解析 = 今天的數字
3. 天花板調到 1 core → **開機失敗,且訊息裡有 App 名字**
4. `per_app.default` 超過自己的 `max` 也要失敗(不能靠搬位置繞過)
5. `disk: "0"` 是「無上限」會停止往下掉;`cpu: 0` 是「未指定」會繼續往下掉(刻意的不對稱,零核心不是任何人的本意)

**部署** 無。

### P2 — 讓 cpu/memory 真的生效 ✅ 已完成(PR #688)

**交付** `SandboxSpec` 帶 `cpu_cores`/`memory_bytes`/`pids_max`;`IsolatedProcessSandbox` 寫進該 item 的 cgroup;registry 改 `spec_for(item)`;`create_app(app_resources=)` 由 `__main__` 注入。

**驗收**

1. 讀 cgroup 的 `memory.max` / `cpu.max` 檔案內容 = spec 給的值
2. spec 只講 memory 時,cpu / pids 仍是部署的值(逐維度回退)
3. registry 交給 `create` 的 spec **逐 item 不同**
4. `memory_bytes=0` 寫進去是 `max`;`None` 才是繼承部署值

**部署** 無 —— ⚠️ 但**只有 local backend 生效**。

**設計註記** spec 層 `None` = 未指定、`0` = 明確無上限,**兩者不能合併**:合併的話,一個刻意解除限制的 App 會反過來繼承部署的限制。

### P3 — http / sandbox-host ✅ 已完成

**交付** `POST /sandboxes` 的 payload 帶資源;sandbox-host 收到就用、沒收到吃自己的 `SANDBOX_HOST_*`;host 那份 `isolated_process.py` 同步改。

**驗收**

1. 用 **contract double** 模擬 host 端契約 —— 只斷言「我方有送」對回歸免疫,不算數
2. host 側測試證明「有帶就用」與「沒帶就吃 env」**兩條路都對**
3. 舊版 host 收到多出來的欄位不會壞

**部署** ⚠️ **必須重新部署 sandbox-host**,否則正式環境完全沒效果。

**地雷** `sandbox-host/` 是獨立專案(自己的 pyproject / uv.lock / CI),**不能 import `workspace_app`**——共用的小工具要各留一份。

### P4 — per-app disk quota ✅ 已完成

**交付** `files/facade.py` 的上限從單一 scalar 改成「該 item 所屬 App 的值」;`create_app(workspace_quota=)` 退成 fallback。

**驗收**

1. 兩個 App 各給不同 disk,**同樣大小**的寫入一個過、一個回 507
2. 「只擋成長」規則原封不動 —— 縮小 / 同大小覆寫 / 刪除在超標時**仍要過**
3. `ensure_room_for` 的整批閘門(資料夾複製、search/replace、staging 一次執行的輸入)走**同一個**值,不能只改單檔那條路

**部署** 無。

**地雷** 這是所有寫入共用的節流點(#245 / #538)。改錯的後果是滿的工作區連清空間都做不到。

### P5 — cpu/mem 帳本 + 准入閘門 ✅ 已完成

**交付** 活體帳(綁 `api/sandbox_activity.py` 的心跳 / 探活)、按 owner 統計;閘門裝在 `chat_send.send`、`workflow_exec`、terminal `POST /exec`;507 + 明確錯誤碼。

**驗收**

1. 超過 `count` 時,新 item 的 turn 在**使用者訊息存進去之前**就被拒(不是 agent 中途才發現)
2. **已經有活 sandbox 的 item 照樣能用**
3. 殺掉一個 sandbox 後額度**自己回來**,全程沒有任何 decrement 呼叫
4. 模擬 pod 猝死(完全不呼叫 `kill`)後,額度仍然回得來

**地雷**

- `api/turns.py:746` 的預熱是 `suppress(Exception)`,**不能當閘門**
- ~~`owner` 要進每個 App 的 `INDEXED_FIELDS`,部署後跑 migrate~~ —— **實作後訂正:不需要**。歸屬是用 `find_work_item` 對 item **點查**(不是對 item 表下 `owner` 述詞),而兩本帳(`_SandboxActivity` / `_WorkspaceDisk`)各自帶著**自己的** `owner` 欄位並索引在自己身上。所以 **item model 不必加索引、不必跑 migrate**,#668 那個陷阱在這裡不成立。
- ⚠️ 但 `list_resources` **會回傳 soft-deleted 的列**,而 `forget` 是軟刪除。查詢一定要帶 `is_deleted == False`,否則被回收的 sandbox 會永遠佔著它 owner 的額度——正是這本帳存在要避免的那個失敗。

### P6 — disk 帳本 + per-user 總量 ✅ 已完成

**交付** measurement 落地成一列(item, owner, bytes, measured_at,索引 owner),來源是現成的 `SandboxSync.on_measured → record_measurement`(目前只放記憶體);`_ensure_headroom` 加第二層檢查。

**驗收**

1. 同一人名下**兩個不同 App** 的 item 加總超標 → 被擋
2. 超標狀態下**刪除永遠成功**(能自己清回來)
3. 第一次量測前會低估 —— 這個視窗要有測試釘住,而不是假裝不存在

**地雷** 不能從耐久快照反推大小(#538 踩過:快照是刻意 additive 的,會漏掉剛建的、又繼續對已刪的收費)。這裡要的是把**已經量到**的數字存下來供加總。

**已知取捨** per-user 總量必然是**略微過期**的數字(以最後一次量測為準),即時加總所有 item 太貴。所以 per-user 那層是近似的,可能短暫超標一點;**per-item 那層仍然即時精準**。

### P7 — per-user 覆寫 + admin 入口 ✅ 已完成

**交付** specstar model(id = user id)、兩層讀取(覆寫 ◇ 全站預設)、admin 設定入口。

**驗收**

1. 改某人的額度,**只有他**改變
2. 沒有覆寫記錄 = 吃全站預設
3. 改完**不用重啟**就生效

### P8 — 「我的資源使用」畫面 ✅ 已完成

**交付** 用量 / 上限;cpu/mem 半列出活著的執行環境 + **關閉**鈕;disk 半列出 item 用量 + **刪除**鈕。

**驗收** 親自按過一輪:**被 507 擋住 → 進畫面 → 關掉 / 刪掉 → 同一個操作重試成功**。

**實測結果**(在真的跑起來的服務 + 真 Chromium 上,不是替身):`per_user.count: 1` → 開第二個 item 的 terminal 回 `507 {"error":"sandbox_quota_exceeded","dimension":"sandboxes"}` → `/my-resources` 顯示「執行環境 1 個 / 1 個 · 第二個 · 1 核 · 512 MB」與「儲存空間 2.9 MB / 80 MB」→ 在瀏覽器點「關閉」→ 變成「0 個 / 1 個 · 目前沒有執行中的環境」→ 同一個 exec 回 200。

**一個刻意的取捨**:disk 那半**不在這裡刪檔**,只列出用量並連到該項目。刪除要在項目自己的檔案清單做,因為那是唯一看得到「正在刪什麼」的地方,而在這裡誤刪是不可回復的。畫面上明講了這件事,也明講「刪除永遠不受額度限制」。

少了這一關,§1.3 的「直接拒絕」是缺一半的決定。

**註** 共用情境下「關閉」是**真的把 sandbox 關掉**,不是只把自己從帳上移除——機器資源只有一份,只移除帳面等於帳是假的。

### P9 — 排程 workflow 的失敗紀錄 ✅ 已完成

**驗收** 定時觸發撞到額度 → 隔天在畫面上**看得到**「因額度不足未執行」,不是靜靜消失。

---

## 3. 最後對帳

| # | 條件 | 狀態 | 憑什麼 |
|---|---|---|---|
| 1 | 兩個 App 設不同 cpu / mem / disk 真的有差 | ✅ | `test_spec_plumbing.py`(cgroup 檔案內容)、`test_per_app_disk.py`(同樣大小一過一擋)、`test_review_regressions.py`(沒宣告就**不送**,別蓋掉 host 的設定)。⚠️ **http 後端要重新部署 sandbox-host 才生效**;契約兩半各自有測試,但沒有在真 host 上跑過 |
| 2 | 開到上限被擋,且擋在 turn 送出前 | ✅ | `test_turn_gate.py` 斷言訊息**沒有**被寫進 store |
| 3 | 被擋的人能自己在畫面上解決 | ✅ | 真服務 + 真瀏覽器實測(見 P8);`test_routes.py` 補端對端 |
| 4 | 額度不會因 pod 猝死 / reap 漏掉而永久蒸發 | ✅ | `test_admission.py` 兩條:`forget` 後回來、**完全不呼叫任何清理**只等視窗過期也回來 |
| 5 | app.json 超過天花板 → 開機失敗並指名 App | ✅ | `test_boot.py`、`test_limits.py` |
| 6 | 什麼都不設的既有部署行為完全不變 | ✅ | `test_review_regressions.py` 釘住「一次寫檔最多一次 item 查詢、零筆帳本寫入」;全套 5461 passed |
| 7 | ⚠️ #687 沒做之前以上全部可被繞過 | ❌ **仍成立** | `owner` 仍是誰都能 PATCH 的欄位 |

**唯一沒有實機驗證的**:第 1 條的 http 後端那一半 —— 需要一個真的 sandbox-host 部署。程式碼兩側都有測試,但「重新部署後兩個 App 真的拿到不同 cgroup」這件事我沒有辦法在這裡證明。

## 3.1 Review 揪出的四個缺口(已修)

第一輪自評把第 1、6 條都判成通過,實際上兩條都是假的。四條都有實測數據,而且都落在「PR 宣稱不會發生」的那一格:

1. **`kind: http` 會靜默蓋掉 `SANDBOX_HOST_*`。** 解析的最底層原本是 `sandbox.isolation.*`(永遠有值),所以每個 item 都送出具體 cpu/memory,host 自己的設定從此無效。兩邊預設都是 512M/1.0 所以測試看不出來;線上只要 host 被調高過,rollout 後每個 sandbox 都會掉回 512M 被 OOM kill。**修法**:cpu/memory 沒人宣告就解析成 `None` —— 這兩個維度的**執行者是後端**,預設本來就該由它決定;disk 由本 app 執行,才該解析成具體數字。副作用要知道:per-user 的 cpu/memory 上限只對「有宣告成本」的 App 生效,`count` 不受影響。
2. **帳本收不到 sandbox 裡產生的位元組。** mirror sweep 的 `on_measured` 只寫記憶體快取。agent 用 `exec` 跑 `pip install` / `git clone` 產出的位元組**永遠**不會進 owner 的總量(不是「落後一次量測」)。**修法**:sweeper 在每輪 mirror 後把量到的數字餵給帳本。注意 `on_measured` 本身要維持同步 —— 第一次修改把它改成 awaitable,結果把耐久 I/O 塞進 mirror 的走訪迴圈,整個測試套件卡死。
3. **每次寫檔多 4 次阻塞式 specstar 查詢 + 1 次耐久寫,即使一個額度都沒設。** **修法**:item→(slug, owner) 加 5 秒 memo(對齊量測本來就有的落後視窗);帳本只在「這個人真的被設了 disk 上限」時才寫。實測回到「一次寫檔 ≤1 次查詢、零筆帳本寫入」。
4. **前端把三種 507 都渲染成「這個工作區滿了」。** 後端刻意分成三種錯誤碼、handler 註解也寫明理由,但 FE 只 branch 在 status。**修法**:`HttpError` 帶上 `code`,訊息選擇抽成純函式 `uploadFailureKey` 並各自有訊息(在這裡刪 / 去別的 item 刪 / 去關環境);順帶把 `MyResourcesPage` 的硬寫中文全部接上 i18n。

**不在本計畫範圍**:owner 轉移 UI(= #687)。

**建議順序** P4 → P5 → P6 → P8 → P7 → P3 → P9。P4 補完「依 App 設定」的最後一維且不用重新部署;P5/P6/P8 是最短的「看得見」路徑;P3 要決定何時重新部署 sandbox-host,可以晚做,但**不做就等於正式環境沒上**。

---

## 4. 順手發現(未在本計畫處理)

`api/registry.py:_acquire` 建 sandbox 時用的是 registry 自己的 spec,而 #674 每回合解析出來的 `tools` shas 掛在 `AgentToolContext.sandbox_spec` 上,那個 spec 只在 `ensure_sandbox_via is None` 的分支才會被用到。若正式路徑的第三方工具是靠 host 自己 resolve 掛載的,那沒問題;若不是,那條路徑值得單獨看一眼。
