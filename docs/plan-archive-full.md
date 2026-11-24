# Plan: 沙盒存檔空間滿了要有人知道，寫不進去就不讓人繼續做

## 問題（2026-11-24，master `2eade995`）

線上事故：存放沙盒檔案的 PVC（archive，`workspaces`）滿了，接著 k8s rollout 重啟 sandbox-host 更新版本，
使用者在 PVC 滿了之後做的修改全部消失。

| 事實 | 來源 |
|---|---|
| 沙盒「現場」放在 sandbox-host pod 自己的暫存磁碟（`SANDBOX_HOST_ROOT`）；pod 換掉就消失。k8s 發的 rollout restart 一定會換掉 pod，這是前提，不是要修的東西 | `deploy/sandbox-host.example.yaml`；user |
| 唯一留得下來的是 archive（`SANDBOX_HOST_NFS_ROOT`，PVC `workspaces`），由 app 叫 host rsync 寫進去：約每 5 秒一次只增不刪的 checkpoint（`mirror_warm`，`api/app.py` 的 `mirror_interval` 預設），每輪結束完整同步（`flush`） | `api/registry.py`、`sandbox_host/nfs_archive.py` |
| archive 滿了以後每一次寫回都失敗：rsync 非零結束 → `RsyncError` → host 的 `/persist` 沒有處理 → 一個沒有內容的 500 → app 只在 log 留 `mirror_warm skipped item … write-back failed` | 同上；`sandbox_host/app.py` 沒有 `RsyncError` 的 handler |
| 沒有人知道：系統不量 archive 還剩多少；使用者照常工作，不知道自己的修改存不進去 | — |
| 失敗的**原因**（例如 `No space left on device (28)`）只在 host 的 log 裡，app 拿不到 | 同上 |

**要守住的狀態不變量**：使用者在 item 裡做的事已經寫進 archive；寫不進去時，那個 item 不能開始新的對話，
而且畫面說得出原始原因。archive 快滿時，運營方要比使用者**先**知道。

## 不做的事（和理由）

| 想法 | 為什麼不 |
|---|---|
| 重啟前把每個沙盒再備份一次、preStop 等它做完 | 治標：只保住最後一次 checkpoint 之後幾十秒的修改，這本來就是現有的保護範圍；archive 滿了時它也存不進去（user 判斷） |
| 限制使用者能存多少（全域上限、排除 `node_modules`） | 使用者的資料是正當的，平台該服務它（user）；排除可重建目錄也違反「回收後原樣回來」的契約（`plan-archive-pack.md`） |
| 把沙盒現場直接放在 PVC 上 | 推翻 #492：NFS 跑現場慢、root_squash 下的多 uid 權限問題 |
| 由 sandbox-host 量剩餘空間、再透過 API 告訴 app | app pod 本來就掛著同一個 PVC（`sandbox.durable.nfs_root`，`deploy/sandbox-host.example.yaml` 的註解寫明同一個 claim 也掛在 app），`os.statvfs` 一次系統呼叫就有；多一個 host→app 的介面只是多一個會漂的地方 |

## 決定

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| D1 | 寫回失敗時擋到哪裡 | **那個 item** 不能開始新的對話（聊天、workflow 步驟、排程都走同一個 turn gate），和工作區額度滿一樣回 **507**；刪檔、上傳、編輯不擋。寫回成功一次就自動解除。正在跑的那一輪不中斷 | 不讓存不進去的工作繼續累積；只擋壞掉的那個 item，其他人不被拖下水（user 同意） |
| D2 | 擋的時候說什麼 | 「目前無法存檔，新的修改會在沙盒重啟時遺失，請聯絡管理員」＋**原因原文**：rsync 的錯誤訊息（最多留前三行錯誤、一行「還有幾行」和 rsync 的總結那一行；每一行本身不截斷），不翻譯不改寫 | user 要原始原因 |
| D3 | archive 快滿時先擋 | 剩餘空間低於**容量 10%**，**所有** item 都不能開始新對話；原因寫「沙盒存檔空間剩 X GB（Y%）」 | 在寫不進去之前就停（user 同意）；用比例，因為各部署 PVC 大小差很多 |
| D4 | 誰先知道 | 低於 **20%** 起，用 `notify()` 通知每個 superuser（`server.superusers`），每跨一級（20%、15%、10%）發一次；回到 20% 以上後重新計算。同時 log 印 warning | 運營方要比使用者早知道，有時間擴 PVC（user 同意） |
| D4′ | 「回升」怎麼算（review round 1 修改，待 user 確認） | 20% 這條線不變；回到 20% 以上並**連續維持 30 分鐘**才算這一次結束 | 剩餘空間在 20% 上下來回（rsync 的寫入和刪除就會造成）時，D4 原本的「回到 20% 以上就重新計算」讓每次來回都重發一輪通知（實測 10 次檢查發了 10 則） |
| D5 | 寄信 | 不另做。`notify()` 寫的每一筆都會經 `server.notification_channel`（`INotificationChannel`）轉送；部署方接了寄信實作，superuser 就同時收到信 | 現成的插拔點（`api/notification_delivery.py`） |
| D6 | 管理頁 | 容量頁（`/capacity`）標題下一行「沙盒存檔空間：已用 / 總量」，資料來自獨立的 `GET /api/admin/capacity/archive`；畫法是共用的 `Gauge`，語意是 WAI-ARIA 的 `meter`（「a scalar measurement within a known range」；APG 的例子是電量、油量），不是 `progressbar`（「the progress status for tasks」，ARIA 在 meter 一節明說不要拿 meter 表示進度）——review round 1 把共用元件的 role 改成 `meter` | superuser 隨時看得到，不必等通知。不塞進 `/admin/capacity` 的 summary：那是整段期間的分析，可能很慢或因資料太多而拒絕，而這一行是 archive 滿的那天最需要看到的 |
| D7 | 門檻設定 | `sandbox.durable.warn_free_ratio`（預設 `0.2`）、`sandbox.durable.block_free_ratio`（預設 `0.1`）；`0` 關掉該項 | 預設值是 user 定的；給運營方調整的餘地 |
| D8 | 沒有 archive 的部署 | `sandbox.durable.nfs_root` 沒設 ⇒ D3–D6 不啟用（開機 log 說一次）；D1–D2 照樣作用在任何寫回失敗 | 量不到就不假裝量得到 |
| D9 | 擋之前先再確認一次（施工時發現） | 有「寫回被拒」紀錄時，gate 先請 registry 把那個 item 活著的沙盒再寫回一次（只增不刪）：成功 ⇒ 清掉、放行；沒有活著的沙盒 ⇒ 沒有東西還沒存 ⇒ 清掉、放行；又被拒 ⇒ 擋；其他錯誤（host 忙、找沙盒位置時 store 出錯） ⇒ 擋。例外：**讀紀錄本身**出錯時當作沒有紀錄、放行並記 log（review round 2）——每個部署的每一次送出都會讀，store 一抖就全部 500 比放過一個被拒的 item 糟；放過的那一輪寫回照樣會失敗，回收前的寫回失敗也不會刪沙盒，所以不會因此丟資料 | 紀錄只會被那個 item 自己的寫回清掉，而寫回只來自握著它 session 的 pod。app rollout 之後沒有 pod 握著它，被擋的對話也不會建立 session——只靠紀錄擋，擴充 PVC 之後這個 item 也會永遠被擋，正好是這次事故的情境 |

## 機制

**寫回失敗的原因要傳得回來（P1）**：host 的 `/persist` 把 archive 的錯誤（`ArchiveError`：rsync 非零結束是它的子類
`RsyncError`——exit 24（複製途中有檔案消失）除外：checkpoint 和 turn 結束時算寫回成功，只有回收前的那一次寫回仍算失敗（review round 2、3）；rsync 之前的小檔寫入失敗——磁碟全滿時最先失敗的就是它——也包成它）回成 JSON 錯誤
（`{"error": "ArchiveWriteFailed", "detail": <原文>}`；`archive_write_failed` 是 app 給前端的 507 code，不是這一個）；
`HttpSandbox.persist` 把**任何** 5xx 都當成這次寫回沒有發生，丟 `ArchiveWriteFailed(reason)`。舊 host 只回沒有內容的
500，原因記成 `the sandbox host gave no reason (HTTP 500)`——擋照樣擋。

**每個 item 的失敗狀態要跨 pod（P2）**：寫回由哪一顆 app pod 做不一定，turn 落在哪一顆也不一定，所以失敗狀態
存在共用的地方：一個不公開的 specstar model（和 `_SandboxActivity`、`_SandboxAddress` 同一種做法），id 就是
item id，內容是原因和時間。寫它的有兩處：`registry._writeback`（`ArchiveWriteFailed` → 寫入；成功 → 只清
**這個 pod 自己記過的**（review round 1：每次成功都清，是每個 warm item 每幾秒、每個 pod 一次 store 呼叫；別的 pod 的紀錄
由 D9 的再確認在要擋之前處理）），以及 `recheck_archive`（D9：再確認成功或沒有活著的沙盒 → 清掉，不論是哪個 pod 記的）。store 自己出錯只記 log，不蓋掉寫回本身的結果。`mirror_warm` 與 `flush` 都經過
`_writeback`，所以 checkpoint 和輪結束的失敗都會被記到。

**turn gate（P3、P4）**：`api/turn_gate.py` 已經是「turn 能不能開始」唯一的地方（`chat_send.send`、
`workflow_exec.drive_turn` 都呼叫它）。兩個 archive 的拒絕由一個 `ArchiveGate`（`api/archive_health.py`）
回答，兩條路共用同一個物件：先看這個 item 的紀錄（D1，by-id 讀取；有紀錄先照 D9 再確認一次），再看剩餘
空間（D3，`shutil.disk_usage`，結果快取 30 秒）。和額度拒絕同一個 507、各自的 code：`archive_write_failed`
（附 `reason`）、`archive_space_low`（附 `free`、`total`、`block_free_ratio`）；不和額度拒絕合併成一句——
資料已經有風險，額度能不能空出來不改變這件事。前端 `web/src/lib/quotaFailure.ts` 的 `archiveRefusal`
在額度文案之前判斷（否則會被讀成「workspace 滿了」，叫人去刪無關的檔案）。

**量空間與通知（P5）**：API pod 的 lifecycle 加一個迴圈，每 60 秒量一次自己掛的磁碟（一次 `disk_usage`），必要時寫
幾列通知。它不是 #804 說的 pod-local 也不是純 producer：讀寫都有上界（一次量測、每個 key 一次有索引的查詢、一列
狀態；寫的列數 ≤ superuser 數 × 3 級 × 跌破次數），用 `ScanLease`（"archive-space"）讓每個時間窗只有一個 pod 做，
和 `notification_delivery_sweeper` 一樣是有理由留在 API 的迴圈（CLAUDE.md 那條慣例已補上）。通知要整個 fleet 只發
一次：`notify()` 的 `dedup_key` 帶「門檻」和「這一次跌破的起點」；起點存在一列共用狀態裡，在 20% 以上**連續 30 分鐘**
才清掉（D4 的修改，見下），下一次跌破才是新的一次。

**量不到不等於空間不足（review round 1）**：mount 出錯（stale handle、路徑不在）或卡住，`measure()` 回 None 並記 log，
gate 不擋、watch 跳過這一輪、容量頁寫「目前量不到」；同時只有一個量測在跑（5 秒逾時）——卡住的 NFS `statvfs`
取消不了，每次送出多開一條 thread 會吃光整個 pod 共用的 executor。

## Phases

- **P1** host：`RsyncError` 回 JSON 錯誤帶原文；`HttpSandbox.persist` 丟 `ArchiveWriteFailed(reason)`；舊 host 的 500 也轉成同一個例外（原因寫明沒有回報）
- **P2** app：每個 item 的寫回失敗狀態（共用 store）；`_writeback` 失敗寫入、成功時清這個 pod 自己記的（別的 pod 的由 D9 的再確認清）
- **P3** turn gate：`archive_write_failed` 拒絕新 turn（聊天、workflow、排程）；前端訊息含原因。D9（擋之前先再確認一次）是 P3 的後續 commit
- **P4** turn gate：剩餘空間低於 `block_free_ratio` 時拒絕所有新 turn（`archive_space_low`）；前端訊息
- **P5** 通知：低於 `warn_free_ratio` 起每跨一級通知每個 superuser 一次；log warning；回到 `warn` 以上並維持 30 分鐘才重新計算（D4′）
- **P6** `/admin/capacity` 顯示 archive 已用 / 總量
- **P7** 文件：`docs/migrations.md` 條目、`docs/configuration.md` 兩個門檻、`docs/deployment.md` 說明通知與寄信插拔點

## 驗證

- 每個 phase 先寫一個在未修程式上會紅的測試，走真的入口（turn gate 經 `POST …/messages`、host 經 `/persist` 路由）。
- 磁碟滿用 rsync 的真實錯誤：host 的整合測試用一個很小的 loopback/tmpfs 目錄當 archive，寫到滿，看 `/persist` 回的原文。
- 守衛要驗證有被套用：把 gate 裡那一行改掉，確認正好對應的測試變紅。
- prod 驗證寫在 PR 與 runbook：在 staging 把 `block_free_ratio` 暫時調高到超過目前用量，確認新對話被擋、訊息帶空間數字，再調回。
