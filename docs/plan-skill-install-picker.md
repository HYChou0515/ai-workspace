# Plan — skill 頁的「安裝到 workspace…」清單要跟打開前端一樣快

Grilled 2026-10-15 on master `15064eba`。每條決定標來源：**[user]** = 使用者決定；**[mine]** = 我定的實作細節。

> 這份計畫推翻 [plan-skill-hub-ux-redo.md](plan-skill-hub-ux-redo.md) D6 的「整張清單等每個 workspace 都標好裝下去會怎樣
> 才出現」；D6 的其餘部分不變：先選 App；已經裝了或已有同名的列不能選，並說明原因；缺工具照樣能選，並寫出缺哪些；裝完留在原頁。
> workspace 裡的安裝選單（[plan-skill-hub-ui-polish.md](plan-skill-hub-ui-polish.md) D8）只看一個 workspace，不受影響。

## 為什麼

使用者：「安裝 skill 到 workspace，連 list 都要載入超久」「它應該跟打開前端一樣快才對」。

清單來自 `GET /skill-hub/entries/{id}/targets?app=…`。改之前，它對你能編輯的**每一個** workspace 讀
`.skill/<name>/`（讀 `.origin`、列檔，每個 workspace 兩到三次）來決定「已經裝了／已有同名／讀不到／可以裝」，全部讀完才回。
實測（master 副本、21 個可編輯 workspace）一次開清單是 47 次檔案操作；每次操作在 http 沙盒上又是「查地址 →
探活 → 真的讀」三段，加上忙碌的沙盒會一路重試，**最慢的那一個決定整張清單什麼時候出現**。打開前端的 item
清單只問資料庫一次。

## 決定

| # | 決定 | 來源 |
|---|------|------|
| D1 | 清單只問資料庫：你能編輯的 workspace，依標題排序，每列只有 id 和標題，**不讀任何 workspace 的檔案**，所以一打開就出現。 | [user] |
| D2 | （被 D3 取代）不事先標狀態：「已經裝了」借右側欄「你裝在哪」的資料，同名衝突等按下安裝才說。使用者看了之後說「這顯然沒有 NN/g」：按了才告訴你會失敗，違反 NN/g 的錯誤預防（Heuristic #5，在使用者承諾之前先檢查並提醒）；狀態晚到時畫面也沒有任何提示（Heuristic #1）。 | [user] → 推翻 |
| D3 | **每一列自己問自己的狀態**：清單出現後，每列各打一次 `GET /a/{slug}/items/{item_id}/skills/install-state?entry_id=`（和安裝同一道權限閘 `edit_content`、同一條判準 `skill_folder_in_the_way`，不喚醒沙盒）。還在查的列顯示小 spinner＋「檢查中…」（NN/g：單一區塊載入用 spinner）；查完顯示「可以安裝」「已經裝了」「已有同名的 skill（誰的）」「已有同名的資料夾」或「現在讀不到，安裝時會再檢查」——那一列的請求本身失敗（例如滾動更新途中打到舊 pod）也算讀不到，不會一直停在「檢查中…」。一個忙碌的沙盒只卡住它自己那一列。 | [user]（「我看到我才會知道」→ 看了 demo） |
| D4 | **檢查中的列可以直接選、直接按安裝**；查完發現不能裝（已經裝了／同名）的列不能選，原因照樣看得清楚（NN/g：停用的選項要說明原因）。先選了、結果才說不能裝 → 選取自動取消、安裝鈕變灰。讀不到的列可選。不論哪種，按下安裝時後端都會再檢查一次，不會覆蓋任何東西。 | [user]（「還沒檢查完的可以直接按吧」） |
| D5 | 版面照 Material 3 的兩行式清單項目：整列是一塊可點的框，第一行是 workspace 名稱，第二行是狀態（附圖示：勾＝已經裝了、資料夾＝同名、時鐘＝讀不到）；選到的列用強調色框＋淡底；不能選的列整塊變淡；清單太長時在對話框裡捲動。 | [mine]；使用者看截圖後確認（「現在這樣 ok」） |

不用「資料庫裡的檔案備份」來查狀態（[mine]，查證後放棄）：正式環境是 http 沙盒＋NFS 檔案樹（`kubernetes/base/configmap.yaml` #492），
工作檔的備份是那棵樹，不在資料庫裡，沒有「一次查詢」可用；而且那棵樹只在沙盒主機回收或存檔時才同步，正在用的 workspace 可能落後很多，標出來的狀態會是錯的。

## 改了什麼

- `api/skill_hub_routes.py`：`targets` 只回 `_editable` 的結果（`SkillHubTarget` 只剩 `item_id`、`title`）；新路由 `…/skills/install-state` 回 `SkillInstallState`（`state` + `owner`）。
- `web/src/pages/SkillHubEntryPage.tsx`：安裝對話框用 `useQueries` 每列各問一次（key 掛在 `["skillHub","targets",…]` 底下，安裝成功的 invalidate 會一起刷新）；狀態文字與圖示；拒絕訊息裡的人用顯示名稱（`describeRefusal` 多收一個可選的 `nameOf`）。
- `web/src/styles/skill-hub.css`：`.skill-hub-target*`（D5）。

## 沒有動的

- 每開一次對話框，每個 workspace 仍各讀兩到三次檔案，只是不擋清單、各列分開。
- `…/installs`（右側欄「你裝在哪」）仍然讀每個 App 的每個可編輯 workspace，在背景載入、不擋頁面。
- `_editable` 仍把那個 App 所有活著的 workspace 連資料讀出來、在程式裡逐筆檢查權限，不分頁；前端的 item 列表則把可見範圍推進查詢裡（`perm/scope.py` 的 `work_item_access_scope`）。workspace 數量變大時這是下一個成本，沒量過，這裡不處理。

## Phases

- **P1** 後端 `targets` 只問資料庫（D1）。
- **P2** 每列各自查狀態、檢查中可選（D3、D4），對話框改版（D5），文件與升級手冊條目。
