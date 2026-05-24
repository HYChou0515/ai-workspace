# 使用者手冊（User Guide）

這是一個 VSCode 風格的缺陷根因分析（RCA）工作台：左邊檔案、中間多分割編輯區、
下方面板（terminal／執行紀錄／agent log）、右邊 AI agent 對話。你和 agent 共用同一個
workspace 的檔案。

---

## 1. 首頁：調查清單

- 表格列出所有調查：title、product、topics、severity、status、owner…。可篩選/排序。
- **New Investigation**：開新調查。需填 title、owner，可選 severity（P0 halt–P4 cosmetic）、status（triaging → awaiting_review → resolved／abandoned）、
  product、topics、members，並選擇：
  - **Template profile**：新調查要 seed 哪組起始檔案（如 `default`、`methodology`、
    `smt-reflow-example`）。
  - **Agent**：綁定哪個 AgentConfig（模型 + prompt + 建議詞）。
- **Templates**：瀏覽可用的範本 profile。

點一列即進入該調查的工作台。

---

## 2. 工作台版面

```
┌────────┬─────────────────────────────────┬───────────────┐
│ 檔案樹  │  編輯區（可水平/垂直分割、拖曳分頁） │  Agent 對話    │
│(sidebar)│  breadcrumb + 分頁列              │  (chat panel) │
│        ├─────────────────────────────────┤               │
│        │  下方面板：output / run history /  │               │
│        │  agent log / terminal / problems  │               │
└────────┴─────────────────────────────────┴───────────────┘
```

### 快捷鍵

| 鍵 | 動作 |
|---|---|
| `Cmd/Ctrl + P` | 命令面板（快速開檔／指令） |
| `Cmd/Ctrl + B` | 切換左側檔案樹 |
| `Cmd/Ctrl + J` | 切換下方面板 |
| `Cmd/Ctrl + S` | 儲存目前檔案 |
| `Ctrl/Cmd + 拖曳分頁` | 複製分頁（而非移動）到另一個分割 |
| 對話框 `Enter` / `Shift+Enter` | 送出 / 換行 |
| `Esc` | 關閉面板/對話框 |

---

## 3. 檔案樹與編輯

- **資料夾** icon 是 chevron（`>`），和檔案 icon 同大小（VSCode 風格）。
- 支援建檔/建資料夾、重新命名、移動、複製、刪除；撞名會提示。空資料夾是「真的」存在
  （不靠隱藏檔）。
- 拖曳檔案/分頁可分割編輯區；不按 Ctrl 拖曳分頁是移動，按住是複製。
- **所有檔案都可開啟編輯**，連 binary 也能開（以 latin1 無損編碼處理）。
- Markdown 檔有 **Edit / Preview** 切換（在分頁列），預覽支援 GFM 表格與 LaTeX 數學式
  （`$ … $`）。
- breadcrumb 可點擊跳目錄。
- 搜尋面板（VSCode 風格）：全文搜尋，可切 regex／大小寫／全字，並用 include/exclude
  篩路徑；可批次 **Replace**。

> **agent 寫的檔案**會在該回合結束後自動出現在檔案樹（不必手動重整）。

---

## 4. AI Agent 對話面板

- 直接打字送出（`Enter`），或點 **建議詞 chips**（如「Draft a 5-Why」「Draft the report」）
  ——這些來自綁定的 AgentConfig。
- agent 的**思考（reasoning）**與**回答**分開呈現，思考預設折疊。
- 回答支援 Markdown 與 LaTeX。
- **工具呼叫**以卡片顯示：執行中即時串流 stdout，結束顯示完整結果（含 exit code）。
- 頂部/底部有 **token 指標**（↑ 送出、↓ 回覆、tok/s、耗時），Claude-Code 風格。
- **模型/agent 切換**：用 picker 選不同 AgentConfig（如本機 Qwen3 vs Claude Opus）。
- 可隨時**停止/中斷**正在跑的回合；送出新訊息會自動取消前一個回合。

---

## 5. 下方面板

| 分頁 | 內容 |
|---|---|
| **Output** | 工具輸出（執行中標 running…，即時更新） |
| **Run history** | 每次工具執行的完整紀錄：指令 + 參數 + 完整輸出 + 耗時/時間，預設折疊 |
| **Agent log** | 完整事件流：每則訊息、每個 tool start/end、token 指標；對話預設折疊 |
| **Terminal** | 直接在 sandbox 內下指令的 shell |
| **Problems** | 問題列表 |

對話窗、run history、agent log 都會自動捲到最底。

> 長時間執行的工具（例如每秒 print 一次的迴圈）會在 **run history／Output** 即時看到輸出；
> 逾時被中止時也會保留中止前已印出的部分。

---

## 6. Terminal

下方面板的 **Terminal** 直接連到該調查的 sandbox（`POST …/exec`）。和 agent 共用同一個
workspace 檔案視圖。

> 互動式程式（如 `vim`、`top`）會被逾時機制中止，不會卡住 terminal。

---

## 7. Notebook

- `.ipynb` 以 cell 形式呈現，可逐格編輯與**執行**（cell 執行有獨立的串流：stdout/stderr、
  圖片等 rich output、錯誤）。
- 可中斷執行、重啟 kernel。
- agent 會幫你寫 cell 程式，但**執行由你在 UI 觸發**。

---

## 8. RCA 報告（8D）

- 報告以版本檔 `/report.vN.md` 存在，**最大 N 為現行版本**。
- 報告檢視器有 **版本切換**、**Generate new version**（請 agent 依現有發現產生
  `v{N+1}`，舊版自動標為 superseded）、**Export PDF**（瀏覽器列印）。
- 看舊版時會有「superseded」浮水印，並可一鍵跳到現行版。

典型 RCA 流程：探索 `/data/*.csv` → 寫 brief / 5-Why / fishbone → 用 notebook 跑分析 →
發現穩定後產出 8D 報告。

---

## 9. 關閉調查

- **Resolve / Abandon**：改調查狀態並關閉 workspace（釋放 sandbox/kernel）。
- **純關閉（Close）**：不動狀態，只關掉 workspace session。

---

## 10. 知識庫（KB）助理

把內部文件交給一個會引用來源的 agent 回答問題。

- **入口**：任何頁面右上的 **Ask agent**（嘴吧 sparkle 鈕）打開「快速問答」抽屜——問完即丟、
  適合臨時查一句；要管理文件或回看完整對話，到 **`/kb`** 頁。
- **Collections（`/kb` → Collections）**：建立具名集合；**Upload** 上傳 md/txt 或 zip/tar，
  **Upload folder** 直接上傳整個資料夾（保留相對路徑，等同解一個壓縮檔）。每份文件顯示**上傳者**
  與**索引狀態**（`indexing…` → `indexed`，背景嵌入完成後自動翻新）；上方搜尋框可依檔名過濾；
  點文件開 viewer，點 ↗ 在新分頁開它的獨立頁面。
- **對話（`/kb` → Chats）**：左側是歷史對話清單，右側是完整對話。**New chat** 開新對話，送出
  第一則訊息後該對話會立刻出現在左側清單。開新對話時可勾選**要搜尋哪些 collection**（預設全選）。
- **引用**：回答中的 `[n]` 會列成可點的來源卡；點下去開該文件並**反白被引用的段落**，文件內的
  相對連結會在 app 內跳轉到對應文件。
- **看得到 agent 在做什麼**：它會自己呼叫 `kb_search`（可多次、依結果再查），工具呼叫與
  「Show thinking」推理可摺疊展開，並顯示 token 速度/總量/耗時——與 RCA 的 agent 面板一致。
- **RCA 調查裡也用得到**：調查的 agent 可透過 `ask_knowledge_base` 查 KB，把含來源的答案帶回分析。

> 需要 Ollama 備好一個 embedding 模型與聊天模型，且用對的 `KB_EMBED_MODEL` 啟動——見
> [deployment.md](deployment.md)。沒有真 embedder 時退回離線雜湊向量（非語意，僅供測試）。

---

## 11. 常見問題

- **agent 寫了檔卻沒看到？** 回合結束會自動重整；若仍沒有，手動在檔案樹做一次操作或重整。
- **agent 用 `python /script.py` 找不到檔？** 需要執行環境支援 user namespace 隔離
  （見 [deployment.md](deployment.md) §4）；否則絕對路徑會打到 host 的根目錄。
- **聊天紀錄不見了？** 若後端用記憶體儲存（預設），重啟後端會清空；要保留請改用持久化
  儲存（見 [deployment.md](deployment.md) §5）。
- **KB 上傳後一直停在 `indexing`？** 嵌入在背景跑，需要 Ollama 有 embedding 模型且
  `KB_EMBED_MODEL` 指對；模型沒裝會卡住或變 `error`（見 [deployment.md](deployment.md)）。
- **KB 答案沒出現 `[1][2]` 引用？** 小模型有時不照做；下方的搜尋結果工具卡仍會顯示找到的來源，
  也可改用更強的 `KB_LLM_MODEL`。
