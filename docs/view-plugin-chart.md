# chart：互動圖表 view plugin（#847 #848）

`view: chart` 讓一個 `*.ai.yaml` 檔案畫成**活的圖表**：可以框選、套索、點圖例、看 tooltip，
AI 主張的那群資料一打開就被點亮。它是平台的第一個 **runtime view plugin**，也是寫 plugin 的範例：
一個資料夾，裡面有前端、沙盒端、skill 與調校情境四個部分，不用改 SPA 原始碼也不用重 build。

## 誰寫、怎麼出現

- **AI 寫**：主張資料裡的關係、趨勢、分布或比較時，AI 會用 `write_file` 寫一份
  `views/<它要表達的>.ai.yaml`，再呼叫 `show_file`。對話裡出現一張卡片，點開就是圖表。
  `show_file` 會先跑 plugin 的 `validate`：檔案不合規格、欄位不存在，或 highlight 一列都沒中 / 全部都中，
  都會被拒絕，卡片不會出現，AI 會拿到錯在哪裡；通過時回覆會多一行摘要，例如
  `highlight matches 3/25 rows; fail_rate 0.02–0.41`。
- **人寫**：同一個檔案格式，手寫也一樣會渲染。

## 規格在哪裡

規格只寫在兩個地方，兩邊由測試綁在一起：

- **給人和 AI 讀的參考**：`view-plugins/chart/skill/SKILL.md`。這份就是 AI 用的 skill，
  範例由測試保證能通過 schema。
- **機器驗證用**：`view-plugins/chart/sandbox-src/src/chart_view/spec.schema.json`，只有這一份。
  沙盒端的 `validate` 和瀏覽器端的 renderer 讀的都是它，`view-plugins/chart/spec-corpus/` 的每一個範例檔同時餵給兩邊，判定必須一致。

一句話摘要：Vega-Lite 子集寫成 YAML（`mark` / `encoding` / `transform` / `layer`），
再加上我們自己的 `source`（CSV、TSV、parquet 或 `{entity: <type>}`）、`keys`、`highlight`、`bin_threshold`。
不認得的鍵一律報錯。

## 連動：有名字的 marking（#856）

- 兩張以上的圖寫同一個 `marking: <名字>`，就連在一起：在其中一張框選、套索或點圖例，其他圖裡對得上的列會亮起、
  其餘變暗。對不對得上看**同名欄位**：選取會投影到 `keys:` 列出的欄位，寫進那個 marking；每張圖用自己資料裡有的
  同名欄位比對。沒有 `keys:` 的圖可以被點亮，但它的選取不會寫進 marking。
- 有 `keys:` 的圖打開時，如果它的 marking 還是空的，檔案裡的 `highlight:` 會當作起始選取寫進去（每次打開只寫一次）；
  已經有人選了就不覆蓋。marking 被清空後，接在上面的圖全部不變暗，不會各自退回自己的 `highlight:`。
- 機制本身不懂任何領域：marking 只是「欄位名 → 一組值」，都是字串。
- view 標頭的 `🔗 <名字> ▾` 可以把這張圖改接到別的 marking、新開一個，或斷開。這是**你自己的畫面狀態**，
  存在瀏覽器裡、不會改寫檔案；要永久改就改 YAML 的 `marking:`。
- 連動的範圍是一個 item。同一個 item 開在好幾個分頁（例如聊天模式開出來的純編輯區頁面）也是同一組 marking。
- AI 可以用 `show_file(layout=…)` 把幾張連動的圖一次放進分割窗格；送訊息時，composer 上方的 marking chip
  會把選取寫成 `.markings/<名字>.json` 給 AI 讀（見升級手冊 [#856](migrations.md#pr-856)）。

## 計算在哪裡跑

聚合、篩選、差異、分箱都在**這個 item 自己的沙盒**裡跑（`exec` 同一套 uid / cgroup 隔離），
瀏覽器只收結果，數值與類別都用二進位 base64 傳。打開圖表時如果沙盒還沒醒，會先喚醒它。

- 沙盒端是一個 tool bundle，用 isolated launcher 啟動：使用者自己 `pip install --user pandas`
  **碰不到**它用的 pandas / pyarrow。
- `source: {entity: <type>}` 跑的是平台自己的 `EntityStore.query`，透過 `view-plugins/sdk-python`
  打包進 bundle，所以圖表看到的紀錄和 table view 完全一致。
- `kind: http`（正式環境）：bundle 已經烤進 sandbox-host 映像的 `builtin/chart`。
  `kind: local`：開機時從 plugin 目錄複製到合併後的 tools root——前提是那個目錄裡有 `sandbox/`。
  API 映像只帶 web 半邊，所以要掛一個用 `view_plugin build` 裝好的目錄，做法見
  [升級手冊 #855](migrations.md#pr-855)。
- `kind: docker`：不支援 tools，所以也不支援 chart 的沙盒端。

## 運營方要知道的事

- **關掉它**：把 `chart` 從 plugin 目錄（`view_plugins.dir`）移除。
  在某個 item 的 skill 偏好把 `chart` 關掉，只會拿掉那份 skill（AI 不再讀得到規格說明）；
  `## Available views` 那兩行仍在，因為它看的是 app 有沒有 `write_file` 與 `show_file`。
- **為自己的模型重調 skill**：
  1. 執行 `uv run python -m workspace_app.view_plugin tune chart`，它會用
     `<plugin 目錄>/chart/scenarios/` 的情境，外加一組不給 skill 的對照組來評分。
  2. 直接改 `<plugin 目錄>/chart/skill/SKILL.md`，重跑一次。

  這份 skill 只有一個檔案，改完**下一輪對話就生效**，不需要 Refresh 或重 build。
  `tune` 會替情境評分，但這些分數不是 CI 的關卡，只是讓你在自己的模型上比較改前改後。
- **prompt 成本**：凡是同時擁有 `write_file` 與 `show_file` 的 app，每一輪 prompt 都會多兩樣東西：
  - `## Available views` 整段，約 280 字元：標題、一句說明，加上 chart 的兩行。csv-table 沒有宣告
    views，所以這整段是因為 chart 才出現；
  - skill 索引裡 `chart` 那一行，約 230 字元。

  SKILL.md 本文約 5.5k 字元，只在 AI `read_skill('chart')` 時才載入。
