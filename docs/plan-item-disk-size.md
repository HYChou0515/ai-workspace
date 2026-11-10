# 一個 item 的硬碟大小，由使用者在環境面板自己調

Grilled 2026-11-10 on master `ef211122`。每條決定標來源：**[user]** = 使用者決定；**[mine]** = 照既有規則推出、
沒另外問的實作決定；**[查證]** = 讀 master 程式碼確認的事實。

> 這份計劃推翻 [plan-item-sandbox-resources.md](plan-item-sandbox-resources.md) §1.2「只有 CPU 和記憶體，
> 沒有硬碟」與它否決「讓使用者也調硬碟」的理由。那份其餘部分不變；
> [plan-item-size-ceiling.md](plan-item-size-ceiling.md) 的天花板規則照用，只是多了一個維度。

## 為什麼

使用者：「如果我想讓 sandbox 可以自己指定大小 是可以的嗎？為什麼當初要設定 20gb per item 但 50 per user」
「對 我們需要讓 user 能自己調整」。

現況〔查證，master `ef211122`〕：

- item 的「環境」面板能調 CPU 與記憶體（`change_permission`），生效值 `min(item 的值, per_app.max, 個人額度)`，
  開環境時才套用（`api/app.py:_resolved_for`）。
- 硬碟上限是 `api/app.py:_quota_for(item)`：只看 App 的值（app.json `resources.disk` → `per_app.default.disk` →
  `filestore.workspace_quota`），**不讀 item 自己的設定**；`per_app.max.disk` 只在開機時檢查 App 的值。
- 每次寫入都經過 `files/facade.py` 讀 `_quota_for`——所以硬碟上限一改就生效，不必重開環境
  （CPU／記憶體是 sandbox 建好就固定，硬碟不是 sandbox 的資源）。
- 只擋成長：縮小、同大小覆寫、刪除永遠放行；工作區已滿時整個 AI 回合被拒（`chat_send.send` /
  `workflow_exec.drive_turn`），刪檔不需要 AI。

舊計劃 §1.2 否決的理由是「調小沒有出口：選了 1G、寫滿了、想改成 512M——要刪他的檔案嗎？」。「只擋成長」本身就是出口
（不刪任何東西，只是不再長），真正的問題只剩「設得比已用量小」那一刻就把自己鎖死——由 D1 擋掉。

## 決定

| # | 決定 | 來源 |
|---|------|------|
| D1 | **不能設得比目前已用量小。** 面板送出前就寫出最小值（目前已用量），伺服器送出時再檢查一次並拒絕。要更小，先刪檔再調。理由：設小於用量唯一的效果是工作區立刻滿、AI 回合被拒，不會是使用者要的（NN/g 錯誤預防：送出前說清楚範圍）。 | [user] |
| D2 | **是上限，不是預留。** 設 20 GB 只表示這個 item 最多長到 20 GB，不先從個人額度扣；各 item 的設定加起來可以超過個人額度，每次寫入照舊再檢查個人額度（先滿就停，訊息說個人額度已滿）。與 CPU／記憶體同一條規則（舊計劃 §1.1）。 | [user] |
| D3 | **天花板是部署的 `per_app.max.disk`**；沒設 `max.disk` 時天花板退回 App 的值（不能往上調）。與 plan-item-size-ceiling D1／D2 同一條規則，同一個地方算（`resolve_app_limits` → `ResourceLimits`）。 | [mine]（比照 #967） |
| D4 | **權限是 `change_permission`**，與 CPU／記憶體同一個欄位組、同一個儲存。 | [mine] |
| D5 | **立刻生效**：存下後下一次寫入就用新上限，不必重開環境；面板寫明這點（與 CPU／記憶體「下次開環境生效」不同）。 | [mine]〔查證：facade 每次寫入讀 `_quota_for`〕 |
| D6 | **面板寫出範圍與個人額度**：硬碟欄位下寫「目前已用 X，最多 Y（這個 App 的上限）」，另寫「個人額度還剩 Z」（有設 `per_user.disk` 時）——上限不是保證，剩多少要看得到（D2 的代價）。 | [mine] |
| D7 | **沒人調過的 item 行為不變**：item 沒有硬碟設定時，上限照舊是 App 的值。 | [mine] |

### 施工時定下的細節（as built）

- **自己的路由** `PUT /a/{slug}/items/{item_id}/disk`，body `{"disk": "5G"}`（大小字串，跟記憶體同一個解析器
  `parse_size`；`0`／`max` 是運營方的「無上限」寫法，使用者不能用，回 422）。不併進 `PUT …/resources`：那條在
  沙盒執行中一律回 409，硬碟不該跟著被擋（D5）。
- **面板的 Save 只送改過的那一半**：只改硬碟就只打 `/disk`，執行中也能存；CPU／記憶體照舊。
- **「個人額度還剩多少」只給 owner**（D6）：那是 owner 所有 item 的加總，給協作者會洩漏別的 item——
  環境面板本來就不透露 owner 的其他 item。
- **欄位是特權欄位**：加進 `perm.checker.ESCALATED_FIELDS`，通用 PATCH、JSON patch、auto-CRUD create、App 的
  create 路由都寫不進去。**發布的副本從 App 的大小開始**，不帶來源的硬碟設定（發布者的大小只管 CPU／記憶體，C10）。
- **沒人調過時寫「預設（這個 App 的大小）」**——CPU／記憶體的預設是「依你目前的額度」，硬碟不是。
- **存下後也重讀對話框下方的「已使用 X / Y」**（demo 時發現 Y 停在舊值）。

## Phases

（施工時依 `/tdd` 每個 phase 先紅後綠；編號連續。）

- **P1** 後端：item 存硬碟設定（與 CPU／記憶體同處）；`_quota_for` 讀它並夾到 D3 的天花板；寫入 API 檢查 D1（≥ 已用量）與
  D3；環境 API 回傳硬碟的設定值、生效值、已用量、天花板、個人額度剩餘。
- **P2** 前端：環境面板加硬碟欄位（D1 最小值、D3 最大值、D5 立刻生效、D6 個人額度）。
- **P3** 文件：本計劃、舊計劃標題下的推翻標記、`docs/design-history.md`、`docs/configuration.md` 6.5、
  `docs/migrations.md` 條目（行為改變：有設 `per_app.max.disk` 的部署，使用者可以把 item 調大）。
