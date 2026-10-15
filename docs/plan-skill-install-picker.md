# Plan — skill 頁的「安裝到 workspace…」清單要跟打開前端一樣快

Grilled 2026-10-15 on master `15064eba`。每條決定標來源：**[user]** = 使用者決定；**[mine]** = 我定的實作細節。

> 這份計畫推翻 [plan-skill-hub-ux-redo.md](plan-skill-hub-ux-redo.md) D6 的「每個 workspace 先標裝下去會怎樣
> （已有同名 → 不能選並說原因）」；D6 其餘（先選 App、缺工具照樣能選並寫缺哪些、裝完留在原頁）不變。
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
| D1 | 清單只問資料庫：你能編輯的 workspace，依標題排序，每列只有 id 和標題，**不讀任何 workspace 的檔案**。「已有同名資料夾」不在清單上先標——按下安裝時，後端本來就會拒絕（`folder_in_the_way`，ui-polish D16），訊息顯示在對話框裡；是看的人讀得到的 hub 副本時會說是誰的（顯示名稱），手寫的資料夾、fork 的起點、讀不到的條目只說「已有同名」。代價：同名衝突要按了才知道；這情況少見。「讀不到」這個狀態一併消失：清單不再問沙盒，忙碌的沙盒和其他列一樣可選。 | [user]（「好像也可以不補」→ 同意） |
| D2 | 「已經裝了」用 skill 頁右側欄「你裝在哪」的同一份資料（`…/installs`，同一個 query key，打開對話框時不重抓，不多讀一次），**只比對同一個 App**；那份資料還沒回來時清單照樣先出，只是暫時沒有標記，回來後補上並把那列設成不能選——先選了那列的話，選取一併取消。 | [user]；後兩點 [mine]（review 第一輪） |

不做的：一列一列在背景補狀態。那樣清單不用等，但每開一次對話框仍要對 N 個沙盒各讀數次，負擔沒變少（[user]）。

## 改了什麼

- `api/skill_hub_routes.py`：`targets` 只回 `_editable` 的結果；`SkillHubTarget` 只剩 `item_id`、`title`。
- `web/src/pages/SkillHubEntryPage.tsx`：安裝對話框用 `installs` 標「已經裝了」；拿掉「已有同名」「讀不到」兩種列上文字（i18n 一併刪）；拒絕訊息裡的人用顯示名稱（`describeRefusal` 多收一個可選的 `nameOf`）。
- `apps/skills.py`：`skill_folder_in_the_way` 的 `wake` 參數沒有 `wake=False` 的呼叫者了，拿掉。

## 沒有動的

- `…/installs`（右側欄「你裝在哪」）仍然讀每個 App 的每個可編輯 workspace，在背景載入、不擋頁面。若它也慢到要處理，是另一件事。
- `_editable` 仍把那個 App 所有活著的 workspace 連資料讀出來、在程式裡逐筆檢查權限，不分頁；前端的 item 列表則把可見範圍推進查詢裡（`perm/scope.py`）。workspace 數量變大時這是下一個成本，沒量過，這裡不處理。

## Phases

- **P1** 後端 `targets` 只問資料庫（D1）＋ 前端對話框改用 `installs` 標已安裝（D2）＋ 本計畫與升級手冊條目。
