# Topic Hub

Topic Hub 是一個圍繞某個主題、長期累積知識的工作區。你把資料丟進來、針對它聊天，再讓它把內容整理成可長期保存的筆記，並歸檔到對應的知識庫。每個 Hub 都會記住之前累積的東西，讓你隨時回來接著用。

---

## 開始使用

1. 建一個 **Topic Hub**，取一個能代表主題的標題。
2. 系統會自動幫你準備好：一份記憶索引 `MEMORY.md`、放筆記的 `memory/` 資料夾，以及這個 Hub 要參考的知識庫清單 `collections.json`。
3. 直接開聊天問問題，或上傳資料後跑 workflow 幫你整理。

---

## 聊天

右側面板上方可以開**多個聊天分頁**：

- **+ New chat** —— 開一個自由聊天。
- **Run workflow** —— 挑一個 workflow 來跑（見下方）。

聊天本身有完整功能：選模型、快捷提示、`@` 提及他人、附加檔案、復原、`Enter` 送出。

回答問題時，它會**由近到遠**找答案：先看這個 Hub 的記憶，再看詞彙表（術語與縮寫的固定解釋），兩者都不夠時，才去搜尋知識庫裡的文件。

---

## 設定要參考的知識庫

每個 Hub 都有一份「要參考的知識庫」清單。要增減有兩種方式：

- **直接跟 agent 說**：「把 *設備履歷* 這個知識庫加進來」或「移除某某知識庫」，它會幫你更新清單。
- **自己編輯**：在左側檔案區打開 `collections.json` 手動增刪。

這份清單同時決定了：聊天時會搜尋哪些知識庫，以及歸檔 workflow 會把文件放進哪些知識庫。

---

## 記憶

- 這個 Hub 的記憶就是檔案：`MEMORY.md` 是隨時帶在 agent 身邊的精簡索引；比較詳細的內容放在 `memory/` 資料夾裡。
- 記憶主要由 workflow 建立與維護，你也可以隨時在檔案區手動編輯。

---

## Workflows

從上方的 **Run workflow** 啟動。每個 workflow 跑完都會把成果寫成檔案，而且可以重複跑。

### 把上傳資料整理進記憶 —— *Digest uploads into memory*

1. 在檔案區的 `inputs/` 資料夾放入要整理的檔案。
2. **Run workflow → Digest uploads into memory**。
3. 它會把每個檔案整理成一篇 `memory/` 筆記，再更新 `MEMORY.md` 索引。

跑完到檔案區看 `MEMORY.md` 與 `memory/`，內容有進去就代表成功了。

### 把上傳文件歸檔進知識庫 —— *File uploads into collections*

> 需要 `collections.json` 裡至少有一個知識庫。

1. 在 `inputs/` 放入要歸檔的文件。
2. **Run workflow → File uploads into collections**。
3. 它會幫每個檔案挑一個知識庫、寫一段摘要，並整理出文件裡的生詞清單，寫進 `glossary.todo.md` 讓你補定義。
4. 在 `glossary.todo.md` 把每個術語下面的定義填好（也可以另開一個聊天請 agent 幫你填），然後回到該 workflow 分頁按 **Continue**（不想繼續就按 Reject）。
5. 核可後，它才會真的把文件歸檔，並把你填好的術語做成詞彙卡。

在你按 Continue 之前，不會有任何東西被寫進知識庫。

### 整理記憶 —— *Consolidate memory*

1. **Run workflow → Consolidate memory**（不需要上傳檔案）。
2. 它會重讀目前的記憶，合併重複、精簡冗長、刪掉過時的內容，重寫成更乾淨的版本。

適合在累積一段時間後，偶爾跑一次做整理。

---

## 你會用到的檔案

| 檔案 | 用途 |
| --- | --- |
| `MEMORY.md` | 隨時參考的記憶索引 |
| `memory/` | 比較詳細的記憶筆記 |
| `collections.json` | 這個 Hub 要參考的知識庫清單 |
| `inputs/` | 放要給 workflow 整理的上傳檔案 |
| `glossary.todo.md` | 歸檔流程產生的術語填空表 |

---

## 協作

任何成員都能進到同一個 Hub 的聊天裡發言、互相用 `@` 提及；訊息會即時出現在正在觀看的人面前。
