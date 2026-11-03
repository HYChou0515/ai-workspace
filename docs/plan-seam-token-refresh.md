# 瀏覽器帶來的 token 也會自動換新；workflow 改過，排程改用改它的人執行

> grill 2026-11-03。每條決定標了來源：**〔user〕** = user 定案；**〔預設〕** = 我自己定、列出來給推翻的
> 實作層決定。推翻 [plan-env-token-refresh](plan-env-token-refresh.md)「自動帶入值（seam）不寫回」那一條，
> [plan-wui-viewer-login](plan-wui-viewer-login.md) D8 與 [plan-schedule-run-as](plan-schedule-run-as.md) D7
> 「workflow 檔被改就收掉綁定」那一半，以及 plan-schedule-run-as G2「workflow 解析得了才綁給作者」那個條件（只有這些）。

## 問題

部署的 `IRequestEnv.env_for` 從瀏覽器的請求讀出 token（下稱 seam），平台把它存在 `PrivateSeam` 那一列，
給沒人在場的執行（goal、re-run、排程、頁面按的 run）用。三件事疊起來，這種 token 過期後就再也用不了：

1. **refresh 跳過 seam**：`env_refresh._one` 看到值來自 seam 就 return（plan-env-token-refresh 的〔預設〕）。
2. **`record_seam` 整列覆寫**：就算平台換了新的寫進那一列，那個人下一次送訊息，瀏覽器帶來的舊值又整列蓋回去。
3. **排程常常是「沒有人」**：`RunIdentity` 沒有那一列 → `acting_for=""` → `unattended_layer` 只給 service 層，
   seam 和 typed 都拿不到。最常見的路：AI 先寫排程、後寫 workflow。寫排程時 workflow 檔還不存在，綁定記
   digest `""`；workflow 一寫出來 digest 就變，sweep 判定 `workflow_changed` 收掉綁定。另一條：寫排程時
   workflow 解析不了，`_credit` 直接跳過，之後那一列「本來就在」，永遠不會再被認作者。

## 決定

| # | 決定 | 來源 |
|---|---|---|
| S1 | seam 的 token 照 #985 的流程自動換新（快過期才換、跨 pod 只換一次、失敗照樣執行），寫回 seam 那一列 | 〔user〕 |
| S2 | 只有 provider **選擇加入**時才換 seam：`IEnvProvider.refreshes_seam = True`。provider 本來就從 `server.env_providers` 設定，不另加 config key；沒加入的 provider 行為不變 | 〔user〕+屬性〔預設〕 |
| S3 | `record_seam` **不倒退** | 〔user〕 |
| S4 | 不倒退的判準：列上多記「請求上次說的值」（`seen`）。這次請求某名稱的值**和上次一樣** → 瀏覽器手上還是舊的，保留列上的（平台換好的，或平台判定失效而清掉的）；**不一樣** → 那個人重新登入過，用請求的；請求不再給的名稱照舊消失（登出就消失）。只要還有名稱沿用列上的值，refresh 多回傳的名稱（瀏覽器不帶的，例如到期時間）也留著；瀏覽器帶的每個名稱都和上次不同、或什麼都不帶（登出）才拿掉（review round 1–2） | 〔預設〕 |
| S5 | 有請求的那一輪也用不倒退後的值（`private_layer` 用 `record_seam` 留下的，不直接用請求的） | 〔預設〕 |
| S6 | seam 的 refresh token 被判定失效（`RefreshRejected`）：從 seam 列清掉那些名稱，回報成 `private` 層——「登入 X」按鈕寫進 Private，seam 不再給那個名稱，Private 的值就會生效；前端不用改 | 〔預設〕 |
| S7 | 一組值有些來自 seam、有些來自 typed：照舊不換（沒有單一一層可以寫回） | 〔預設〕（沿用 #985） |
| W1 | **workflow 被改過，排程改用改它的人執行**——在知道是誰的門上（**那個人剛送出的聊天訊息**那一輪裡 AI 的 `save_workflow`／`write_file`／`edit_file`——pod 掛掉後被接手重跑的同一則也算；檔案 PUT：檔案編輯器、檔案樹上傳、Workflows 面板的匯入、WUI 頁面的 `writeFile`；從範本複製（#520））。改的人在 item 沒有 `execute`、或從認不出是誰的門改（goal 自動回合、workflow 節點或排程自己的執行、exec、zip 匯入、檔案樹的複製／搬移、尋找取代、Run 附帶的上傳、刪除、超過 1 MiB 的 PUT）→ 照舊：sweep 在下一次觸發時收掉綁定並通知。**只在檔案此刻還是他寫的那份時轉交**（兩個人前後存，先存的人不會接到後存的人的內容）；轉交前再讀一次綁定，這期間有人按「停止用我的身分」或被改指定就不動（review round 2） | 〔user〕+門〔預設〕 |
| W2 | 只換**已經綁定**的列；按過「停止用我的身分」的列（沒有綁定）不會因為有人改 workflow 就被接走 | 〔預設〕 |
| W3 | 換了人 → 原因記成 `workflow`（畫面：「它跑的 workflow 是他改的」），**兩個人都通知**：原本的人（`schedule_binding_replaced`）、接手的人（`schedule_binding_assigned`，講可以「停止用我的身分」）；同一個人 → 只更新 digest、保留原因，不通知 | 〔預設〕 |
| W4 | 寫排程時 workflow 解析不了：照樣認作者，digest 記一個永遠對不上的值。解析不了的列 sweep 本來就不跑；之後由 W1 的門修好就換成修的人，從認不出的門修好就照舊收掉。**不記 `""`**：那是「profile 的 workflow」，檔案一刪就會讓同名 profile workflow 帶著值跑（viewer-login round 6） | 〔預設〕 |

## 機制

**S4 的列**：`PrivateSeam` 加 `seen: dict[str, str] | None = None`（`None` = 舊列，`values` 就是請求說的）。
`record_seam(fresh)` 回傳要用的值：

```
for 名稱, 值 in fresh（照 fresh 的順序）:
    上次 == 值 → 列上有就用列上的，列上沒有（被清掉）就不給
    否則      → 用 fresh 的
寫回：values = 上面的結果、seen = fresh；兩者都沒變就不寫
```

refresh 寫回 seam 用 `replace_seam`：只改 `values`，`seen` 不動——所以下一次請求帶同樣的舊值時，對得上 `seen`，
保留換好的。

**W1 的掛點**：`ScheduleAuthorship.workflow_written(item, workflow_id, author=, written=)`，寫完 workflow 檔之後呼叫，
**不會丟例外**（和 `credit` 一樣：檔案已經寫進去了）。它讀 item 的綁定，按 `path` 讀那份排程檔，找出
`row.run == workflow_id` 且 digest 和現在不同的列；作者有 `execute` 時，換了人就 `bind(..., reason="workflow")` 並通知兩個人
（`on_moved`），同一個人只更新 digest、保留原本的 reason／assigned_by，不通知。

**W1 的位元組**：每扇門把「這次寫進去的位元組」交給 hook（PUT 從暫存檔讀，超過 `FOLLOW_MAX_BYTES`＝1 MiB 就不讀、不轉交；
`edit_file` 用寫入前讀到的內容算；`save_workflow`／範本複製用同一個編碼函式 `workspace_workflow_bytes`）。hook 用 build 的
digest 定義算它和即時檔案，不相等就不動。

## 已知限制（review round 1–2，不修）

- **`record_seam` 和 refresh 的寫回幾乎同時**：後寫的贏（和既有 Private 層同一個規則）。refresh 寫回落在
  `record_seam` 讀列和寫列之間時，換好的值會被蓋回瀏覽器的舊值一次；refresh token 會 rotation 的系統，下一次換會被拒、
  那個人要重新登入一次。
- **滾動更新期間**：舊版 pod 的 `record_seam` 整列覆寫、把 `seen` 弄丟，效果同上一條。
- **每個 item 各一份**：seam 列是 (人, item) 一份，兩個 item 會各自拿瀏覽器那顆 refresh token 去換；rotation 的系統，
  後換的會被拒。寫在 `refreshes_seam` 的說明裡。
- **一組名稱裡瀏覽器只換了一個**：S4 是逐名稱判斷，組合可能是瀏覽器的新值配平台換好的另一個值。只影響選擇加入的 provider。
- **重新登入時有名稱沒變**：瀏覽器重新登入後若仍帶著某個和上次一樣的名稱（例如使用者 id），refresh 多回傳的名稱
  （例如舊的到期時間）會留在新 token 旁邊，直到下一次換新覆寫它。只影響選擇加入的 provider。
- **列上同時有兩份憑證**：換好之後，`seen` 存著瀏覽器那份、`values` 存著換好的那份；同一列、同樣只有本人讀得到。
- **sweep 在寫檔和 hook 之間觸發**：落在寫檔完成到 hook 讀綁定之間時，那一次會收掉綁定（照舊通知），hook 之後找不到綁定，
  排程變成沒有人；落在 hook 讀綁定之後，hook 轉交前的重讀會看到綁定已經不在，也不動。
- **重讀和改寫之間**：轉交前的重讀不是原子操作，重讀到 `bind` 之間（一次讀取的時間）有人改綁定，仍會被覆寫。
- **頁面放在 `.workflows/` 裡**：那個頁面的 `writeFile` 能改 workflow 檔；按的人若有 `execute`，排程就轉給他（他會收到通知）。
  和頁面自己的排程檔被 G2 認作者是同一類。
- **路徑寫法**：`.workflows//x.json` 之類 sandbox 認得、`workflow_id_of` 不認得的寫法，當成認不出是誰的門（收掉綁定）。

## Phases

| Phase | 內容 | 驗收 |
|---|---|---|
| P1 | `PrivateSeam.seen`、`record_seam` 不倒退並回傳要用的值、`replace_seam`、`private_layer` 用回傳值 | 請求重複舊值時保留換好的；請求帶新值時用新的；請求不給的名稱消失；舊列（無 `seen`）照舊；有請求的那一輪拿到保留後的值 |
| P2 | `IEnvProvider.refreshes_seam`；`env_refresh` 對加入的 provider 換 seam、寫回 seam 列；失效清 seam、回報 `private` | 加入：seam 值快過期時工具拿到新值、列上是新值、`seen` 不變；沒加入：照舊不換；失效：seam 列清掉、回報 private；混合來源不換 |
| P3 | W1–W4：`workflow_written` 接到五個入口（`save_workflow`／`write_file`／`edit_file`、檔案 PUT、範本複製）；解析不了的列記對不上的 digest | 每扇門一條：別人改 workflow 後排程用改的人、原本的人收到通知；同一個人只更新 digest；沒 `execute` 不換；沒綁定的列不被接走；先寫排程、後寫 workflow → 排程用寫的人 |
| P4 | 文件：runbook 條目、design-history、三份舊 plan 標註、`extending-the-platform.md`（`refreshes_seam`） | `tests/docs/test_docs_index.py`；`mkdocs build --strict` |
