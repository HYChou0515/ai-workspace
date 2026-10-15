# Plan — skill 頁的「安裝到 workspace…」清單要跟打開前端一樣快

Grilled 2026-10-15 on master `15064eba`。每條決定標來源：**[user]** = 使用者決定；**[mine]** = 我定的實作細節。

> 這份計畫推翻 [plan-skill-hub-ux-redo.md](plan-skill-hub-ux-redo.md) D6 的「整張清單等每個 workspace 都標好裝下去會怎樣
> 才出現」；D6 的其餘部分不變：先選 App；已經裝了或已有同名的列不能選，並說明原因；缺工具照樣能選，並寫出缺哪些；裝完留在原頁。
> workspace 裡的安裝選單（[plan-skill-hub-ui-polish.md](plan-skill-hub-ui-polish.md) D8）只看一個 workspace，不受影響。

## 為什麼

使用者：「安裝 skill 到 workspace，連 list 都要載入超久」「它應該跟打開前端一樣快才對」。

清單來自 `GET /skill-hub/entries/{id}/targets?app=…`。改之前，它對你能編輯的**每一個** workspace 讀
`.skill/<name>/`（讀 `.origin`、列檔，每個 workspace 兩到三次）來決定「已經裝了／已有同名／讀不到／可以裝」，全部讀完才回。
實測（master 副本、21 個可編輯 workspace）一次開清單是 47 次檔案操作；在 http 沙盒上，一個不在這個 pod 有
session 的 workspace，每次操作又是「查地址 → 探活 → 真的讀」三段，加上忙碌的沙盒會一路重試，**最慢的那一個決定
整張清單什麼時候出現**。打開前端的 item 清單只問資料庫一次。

## 決定

| # | 決定 | 來源 |
|---|------|------|
| D1 | 清單只問資料庫：你能編輯的 workspace，依標題排序，每列只有 id 和標題，**不讀任何 workspace 的檔案**，所以一打開就出現。 | [user] |
| D2 | （被 D3 取代）不事先標狀態：「已經裝了」借右側欄「你裝在哪」的資料，同名衝突等按下安裝才說。使用者看了之後說「這顯然沒有 NN/g」：按了才告訴你會失敗，違反 NN/g 的錯誤預防（Heuristic #5，在使用者承諾之前先檢查並提醒）；狀態晚到時畫面也沒有任何提示（Heuristic #1）。 | [user] → 推翻 |
| D3 | **每一列自己問自己的狀態**：清單出現後，每列各打一次 `GET /a/{slug}/items/{item_id}/skills/install-state?entry_id=`（和安裝同一道權限閘 `edit_content`、同一條判準 `skill_folder_in_the_way`，不喚醒沙盒）。還在查的列顯示小 spinner＋「檢查中…」（NN/g：單一區塊載入用 spinner）；查完顯示「可以安裝」「已經裝了」「已有同名的 skill（誰的）」「已有同名的資料夾」或「現在讀不到，安裝時會再檢查」——那一列的請求本身失敗（例如滾動更新途中打到舊 pod）也算讀不到，不會一直停在「檢查中…」。一個忙碌的沙盒只卡住它自己那一列。 | [user]（「我看到我才會知道」→ 看了 demo） |
| D4 | **檢查中的列可以直接選、直接按安裝**；查完發現不能裝（已經裝了／同名）的列不能選，原因照樣看得清楚（NN/g：停用的選項要說明原因）。先選了、結果才說不能裝 → 選取取消（之後那列就算又變成可裝也不會自己選回來），對話框寫出「「X」已有同名的資料夾，已取消選取。」（`role="status"`，讀螢幕軟體也會念）、安裝鈕變灰。讀不到的列可選。不論哪種，按下安裝時後端都會再檢查一次；被拒（409）時那一列會重新檢查，不會在拒絕訊息旁邊還寫「可以安裝」。檢查和寫入之間不是原子的，這是安裝路由原本就有的縫，不在這裡處理。 | [user]（「還沒檢查完的可以直接按吧」）；後兩點 [mine]（review 第二輪） |
| D5 | 版面照 Material 3 的兩行式清單項目：整列是一塊可點的框，第一行是 workspace 名稱，第二行是狀態（附圖示：勾＝已經裝了、資料夾＝同名、時鐘＝讀不到）；選到的列用強調色框＋淡底；不能選的列底色、框線、名稱變淡，狀態那行保留顏色好讀；清單太長時在對話框裡捲動。 | [mine]；使用者看截圖後確認（「現在這樣 ok」） |
| D6 | **同時最多 4 列在查**，關掉對話框就把還沒回來的問題撤掉。忙碌的沙盒一個問題可能要幾分鐘；不設上限時，幾列就能佔滿瀏覽器對同一個主機的連線（HTTP/1.1 是 6 條），連按下的「安裝」都要排隊。4 是我們的判斷（留兩條給安裝與頁面），不是查來的數字。安裝成功時，各列的答案只標成過期、不立刻重查（對話框正在關），下次打開才重新問。 | [mine]（review 第二輪） |

不用「資料庫裡的檔案備份」來查狀態（[mine]，查證後放棄）：正式環境的沙盒是 http（CLAUDE.md #366）；若搭配 #492 的 NFS 檔案樹
（`kubernetes/base/configmap.yaml` 裡的選配做法），工作檔的備份是那棵樹，不在資料庫裡，沒有「一次查詢」可用。就算備份在資料庫，
週期同步（`create_app` 預設每 5 秒）只增不刪（`delete=False`），刪掉的同名資料夾要到這輪對話結束或回收才會從備份消失，那段時間
標出來的「已有同名」會是錯的。

## 改了什麼

- `api/skill_hub_routes.py`：`targets` 只回 `_editable` 的結果（`SkillHubTarget` 只剩 `item_id`、`title`）；新路由 `…/skills/install-state` 回 `SkillInstallState`（`state` + `owner`）。
- `web/src/pages/SkillHubEntryPage.tsx`：安裝對話框用 `useQueries` 每列各問一次，經 `createLimiter`（`web/src/api/concurrency.ts`）限同時 4 個、帶 abort signal（D6）；key 掛在 `["skillHub","targets",…]` 底下，安裝成功時 `invalidateHubInstalls` 以 `refetchType: "none"` 標成過期；狀態文字與圖示；選取被取消的提示；拒絕訊息裡的人用顯示名稱（`describeRefusal` 多收一個可選的 `nameOf`）。
- `web/src/styles/skill-hub.css`：`.skill-hub-target*`（D5）。

## 沒有動的

- 每次重新檢查（打開對話框時答案已過期，全域 `staleTime` 是 30 秒），每個 workspace 仍各讀兩到三次檔案，只是不擋清單、各列分開、同時最多 4 列。
- 某一列的請求被拒（例如清單載入後權限被收回 → 403、條目被下架 → 404）也顯示「現在讀不到，安裝時會再檢查」——按下安裝時確實會再檢查並拒絕，只是沒說出真正原因。少見，不另做文字。
- `…/installs`（右側欄「你裝在哪」）仍然讀每個 App 的每個可編輯 workspace，在背景載入、不擋頁面。
- `_editable` 仍把那個 App 所有活著的 workspace 連資料讀出來、在程式裡逐筆檢查權限，不分頁；前端的 item 列表則把可見範圍推進查詢裡（`perm/scope.py` 的 `work_item_access_scope`）。workspace 數量變大時這是下一個成本，沒量過，這裡不處理。

## Phases

- **P1** 後端 `targets` 只問資料庫（D1）。
- **P2** 每列各自查狀態、檢查中可選（D3、D4），對話框改版（D5），文件與升級手冊條目。
- **P3** review 第二輪：同時最多 4 列、關閉即撤（D6）；安裝成功不立刻重查；被拒時重查那一列；選取被取消要說出來。
