# Plan — #366 sandbox address coherence (http sandbox-host)

> 症狀：聊天室開著一段時間後 filetree 什麼都沒了、terminal `ls` → `sandbox not found`。
> 定案 via `/grill-me`。線上跑 **http sandbox-host**（非 `kind: local` 共用 PVC）。

## Problem

三個症狀，同一病根「**app 信任過期的本地狀態；備份分不清『暫時空』與『真的刪』**」：

1. **臉 A `sandbox not found`** — sandbox-host 閒置 30 分（`SANDBOX_HOST_IDLE_TTL`）`rmtree` 沙盒；app 握著舊地址（`InvestigationSession.handle`）不重驗、不重建（`ensure_handle` 在 `handle != None` 短路）→ exec 打死地址 → `SandboxNotFound`。
2. **臉 B filetree 變空** — `SandboxSync.mirror` deletion-aware，對「空/半重建」沙盒 diff → 把 durable snapshot 整份刪光 → `restore` 讀到空 → 永久遺失。
3. **多 pod 分裂** — 每個 app pod 各自 `create`（http `create` 是 uuid-keyed，`sandbox_id` 被忽略）、各存各的 in-memory 地址 → 同一 item 兩份 dir、互相蓋。現靠 nginx sticky 勉強擋。

單 pod 不會爆（reap→fresh session→create+restore 自癒）；本 bug 是多 app pod。

## Protocol（LOCKED）

原則：**不共用資料夾；共用的是「地址」不是「資料夾」**。一個 item 任何時刻只有一份沙盒；備份只在沙盒「完整可信」（有 `.ready`）時才敢刪；拆除資料前先原子標記「不可信」（先刪 `.ready`）。

specstar 每個 item 存：①檔案 snapshot（既有 FileStore）②目前沙盒地址（handle，**新增**）。

- **取用沙盒**：讀 specstar 地址 → 打不到（IP 死 / 沒此 item → `SandboxNotFound`）→ **CAS 重建**：`create` → `restore` → 寫 `.ready` → **CAS 把新地址寫回 specstar**（expected=舊值）；搶輸 → `kill` 自己剛開的孤兒沙盒、改讀贏家地址。順序：**倒回完成 → 寫 `.ready` → 才公布地址**。
- **備份三明治（mirror sandbox→specstar）**：
  1. gate1 `exists(.ready)`？否 → 跳過整輪。
  2. `walk`。
  3. 上傳/更新變動檔（add/update，永遠安全）。
  4. gate2 `exists(.ready)` 還在？否 → **跳過刪除**（本輪只上傳）。
  5. 兩 gate 都過 → 套用刪除（specstar 有、walk 沒有 = 使用者真的刪了）。
  6. 碰 `SandboxNotFound` → 乾淨跳過，不 crash。
- **明確刪除**（檔案工具/UI）：當場**雙寫**（沙盒 + specstar）。shell `rm` 靠備份三明治傳播。
- **回收**（host reap/kill）：**① 先 `unlink(.ready)` → ② 再 rmtree**。（`rmtree` 順序不定，若 `.ready` 最後才刪 → 「`.ready` 還在但檔案缺一半」窗口 → 備份誤刪。）
- **韌性**：所有 sweeper 迴圈吞單一 item 例外、續跑，絕不整 task 死。

順序總結：**倒回完成→寫 `.ready`→公布地址**；**回收→先刪 `.ready`→再刪內容**；**備份→`.ready` 前後各驗一次才敢刪**。

## Phases（flat, TDD）

測試骨架：多個 app registry 共用「一個 specstar（存地址）+ 一個 FileStore」+ 可注入 sandbox（模擬 host reap / 空殼 / 半 restore）。

- **P1** — 沙盒地址移到 specstar + CAS：per-item handle 持久化、CAS 更新；registry 以 specstar 為權威（in-memory 只當快取）。測試：兩個 registry 共用一 specstar → 只收斂到**一個**地址。
- **P2** — 自癒重建：ensure-live-sandbox 讀地址→探測→失效(`SandboxNotFound`)→ create+restore，倒回完成才 CAS-publish；搶輸清孤兒。測試：地址失效 → 終端/turn 自動復原、無雙開、無殘留孤兒。
- **P3** — `.ready` + mirror 三明治：restore 完成寫 `.ready`；備份 gate1→walk→上傳→gate2→刪除。測試：對「空/半 restore」沙盒 mirror **不刪** specstar；對完整沙盒 shell-rm **會**傳播刪除。
- **P4** — 回收先刪 `.ready`：sandbox-host reap/kill = `unlink(.ready)`→rmtree。測試：reap 進行中的 walk 被 gate2 擋下、不誤刪。
- **P5** — 明確刪除雙寫：`facade.delete` warm 也刪 specstar。測試：工具刪除立即反映到 specstar。
- **P6** — sweeper 韌性：迴圈吞單 item 例外續跑。測試：一個殭屍地址不會弄死 reaper/mirror task。
- **P7** — 收尾：關 #372（已關）、標 #369、更新 CLAUDE.md/docs、跑全套 + 100% coverage gate。

## 關聯

- **#372**（shell-rm 傳播）→ `.ready` gate 已能安全保留刪除，**已關閉不需要**。
- **#369**（NFS `setfacl` Operation not supported）→ 只有走「耐久 NFS 共用 dir」路線才需解（改 chown + no root_squash）；本方案繞開，**不需要**。
