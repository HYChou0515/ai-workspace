# Plan — skill hub 好用、清楚：版面與資訊架構重做

Grilled 2026-10-08 on master `7d666c36`（含 #875 版本歷史）。每條決定標來源：**[user]** = 使用者決定；
**[mine]** = 我定的實作細節，列出來給推翻。

> 這份計畫推翻 [plan-skill-hub.md](plan-skill-hub.md) 的 Q4「根在上、fork 收在原作底下」（列表不再巢狀放 fork，D2）
> 與 Q7 的按鈕位置（管理動作收進「管理 ▾」，D5）；其餘不變。

## 為什麼

使用者：「Skill 顯示方式與列表排列方式都有很大改進空間，你查一下 uiux 守則，你幾乎沒照做對嗎」。

[plan-skill-hub-ui-polish.md](plan-skill-hub-ui-polish.md)（D1–D17）只修了零件（留白、焦點、toast、圖示、文案），
**版面與資訊架構從沒對照過成熟的做法**。2026-10-07 在 master `e1e1a0f9` 用真 app、真 Chromium 1280／390
盤點（108 張截圖，含量級：985 個檔案的 skill、約 207 筆、40 個版本、20 個 fork、15 個工具），量到：

- 列表一欄卡片，fork 巢狀卡中卡（一個原作 20 個 fork = 2,824px），約 207 筆全畫出來 = 23,175px 高；
  兩組切換鈕長一樣、沒標籤；同一個人寫兩次（`default-user/` 與「You」）；每張卡都掛同一個「Playground」。
- 條目頁沿用「我的資源」的 `.page`（`max-width: 760px`，表單頁的寬度）；管理按鈕排在描述前面；SKILL.md
  埋在工具與審查之後；985 個檔案畫成 985 顆不能點的膠囊（頁高 15,204px／390 時 32,516px）；40 個版本 40 張
  大卡、39 顆橘色「回復」；版本只用日期命名。
- 看起來能點但不能點的元素 10 類（檔案／工具膠囊、可見範圍膠囊、fork 數、App 標籤、狀態標籤、整張卡）；
  反過來，對話卡片標題能點卻不像連結。
- workspace 裡的 skill 面板：640px、標籤被按鈕蓋住或在 390 被切 12px、每列 1–5 顆圖示對不齊、內部值
  `shared`／`workspace` 直接顯示；從 skill hub 裝是 modal 疊 modal。

## 決定

| # | 決定 | 依據 | 來源 |
|---|---|---|---|
| D1 | 列表改成**緊湊列表**：一列一個 skill，名稱＋一行截斷說明在左，owner、次數、更新時間靠右 | NN/g〈List vs. Grid〉：文字為主、要逐項比較時用列表；我們的 skill 沒有圖示 | [user] |
| D2 | 列表**只放原作**；原作那列寫「N 個 fork」並連到原作頁的 fork 分頁。搜尋與「我的」照常出現 fork，那列標「fork 自 …」 | GitHub：搜尋預設不含 fork、repo 頁只給 fork 數 | [user] |
| D3 | 列表**不標「已安裝」**；skill 頁右側欄列出「你在這些 workspace 裝了它」 | 「已安裝」只對某個 workspace 有意義 | [user] |
| D4 | 排序：名稱／最常使用／**最近更新**；篩選只加 **owner**；每次載入 50 筆、「載入更多」、顯示「共 N 個」 | NN/g〈Infinite Scrolling〉：頁尾到得了、位置回得去 | [user] |
| D5 | skill 頁：**分頁籤＋右側欄**。分頁＝說明（SKILL.md，預設）／檔案／版本紀錄／fork；右側欄＝「安裝到 workspace…」、你裝在哪、權限、次數、更新時間、用到的工具（標這個 App 沒有的）、AI 審查意見。自己的 skill 頁首多「管理 ▾」 | VS Code Marketplace（Details／Changelog）、npm（Readme／Code／Versions）；Material／HIG：次要與破壞性動作收進選單 | [user] |
| D5a | **頁面寬度等 web demo 實際看過再定**：寬度是 CSS 變數，demo 時一起調 | — | [user] |
| D6 | 「**安裝到 workspace…**」：對話框先選 App、再列你能編輯的 workspace；每個 workspace 先標裝下去會怎樣（已有同名 → 不能選並說原因；這個 App 缺工具 → 能選並寫缺哪些）；裝完留在原頁，顯示「已裝進 X ・ 打開 workspace」，右側欄更新 | 與 fork 對話框同一個選擇器；判準與 workspace 裡的安裝選單（ui-polish D8）同一條 | [user] |
| D7 | 全站畫面文字 **item → workspace**（17 句繁中逐句確認；意思不是 workspace 的列出來問）。程式碼與開發文件照舊用 item。**不加守衛** | item 是開發用語 | [user] |
| D8 | 版本**系統自動編號** v1、v2…＋日期時間；只有改內容的事件（發布、回復）佔版號，權限／轉移是一行註記 | 版號用來分辨、指稱；skill 沒有相依關係，不需要 semver | [user] |
| D9 | workspace 裡的 skill 面板：保留 modal；每列兩行（名稱＋狀態文字／說明）；右邊固定「套用」「預設／開啟／關閉」「⋯」，下載／發布／還原／更新收進「⋯」；要處理的狀態是「文字＋動作」（例「有新版 ・ 更新」）；「從 skill hub 裝」改成面板內的一頁（有返回） | NN/g〈Icon Usability〉；Material 3 overflow menu；Polaris／GOV.UK：要處理的狀態要附動作 | [user] |
| D10 | **保留「下架」與「權限設定」兩顆**；「下架」補確認對話框與完成提示；兩者對已裝者的影響說明共用一份文字；「可見範圍」全面改名「**權限設定**」 | NN/g #5 錯誤預防、#1 狀態可見 | [user] |
| D11 | 用詞：**skill**、**owner**、**fork** 不翻譯（面板標題「技能」→ skill、「擁有者」→ owner、「條目」只在開發文件）；item → workspace（D7）。**不加守衛** | 一個概念一個詞 | [user] |
| D12 | 檔案分頁：先一行摘要「N 個檔案 ・ 大小 ・ 含 M 個 script」，下面是**資料夾預設收合的樹**，點檔案看內容；**不用膠囊** | GitHub repo 樹、npm 只給數量與大小；Material 3 Chips 不是拿來列資料的 | [mine] |
| D13 | 比對版本：先「新增 a／修改 b／刪除 c」摘要清單，逐檔點開，+/− 紅綠、去掉 `--- a/ +++ b/ @@` 原始標頭，標題「v3 → v5」 | GitHub PR Files changed | [mine] |
| D14 | 版本紀錄：每版只顯示改了什麼，預設最近 5 版、可展開全部；「回復到這一版」是次要按鈕；凡指稱某版一律「v N ・ 日期 時間」 | Material 3：一個畫面一個主要動作；漸進揭露 | [mine] |
| D15 | 看起來能點的一律能點，否則畫成一般文字：工具名連到 skill 頁說明分頁的工具段（右側欄）、fork 數連到 fork 分頁、整列是連結；App 標籤只在 skill 頁出現一次（「來源 App」）；對話卡片標題畫成連結 | NN/g〈Beyond Blue Links〉 | [mine] |
| D16 | 錯誤分兩種：不存在／已刪除／沒有權限 → 說清楚、給回列表的連結、不放「再試一次」；網路錯誤才有「再試一次」 | NN/g #9、GOV.UK page not found | [mine] |
| D17 | 小修：搜尋沒結果時「清除搜尋」並說明只搜名稱與說明；權限設定、轉移、fork 被拒時不露內部 id 與路徑；審查意見只出現一次並渲染 markdown；SKILL.md 方框不再重複大標題；次數為 0 不顯示；轉移對話框與「開新 workspace」按鈕的間距 | 盤點 #21、#28–#35 | [mine] |

**不做**：App／工具篩選（D4）；無限捲動（D4）；列表上的「已安裝」（D3）；用詞守衛（D7、D11）；
全站其他頁面的版面重做（只動 skill hub 相關畫面與 D7 的用詞）。

## 機制

### 後端

- **列表** `GET /skill-hub/entries`：新增 `owner`、`offset`、`limit`（預設 50）、`sort=updated`；回
  `total`。瀏覽（沒有 `q`、不是 `mine`）只回原作，每列帶 `fork_count`；有 `q` 或 `mine` 時 fork 與原作
  平列、各自帶 `forked_from`。`forks` 巢狀欄位拿掉。
- **最近更新**：`SkillHubEntry` 加 `content_at: datetime | None`，發布與回復時寫入（改內容的那兩條路）；
  只加有預設值的欄位，舊列 `None` → 排在最後、不顯示時間；不回填（下一次發布就有）。
- **版號**：`history` 依時間由舊到新，給 `publish` 與 `rollback` 事件 `version = 1, 2, …`；其他事件 `None`。
- **檔案**：版本與條目回 `files: [{path, size}]`（`ls-tree -l`）與 `scripts`（可執行或 `scripts/` 下的數量）。
- **你裝在哪**：`GET /skill-hub/entries/{id}/installs` 回 viewer 能編輯的 workspace 裡，副本 `.origin` 指向這個
  條目的那些（App、workspace、名稱）。
- **安裝目標**：`GET /skill-hub/entries/{id}/targets?app=<slug>` 回該 App 裡 viewer 能編輯的 workspace，每個帶
  `state: ok | name_taken | installed` 與 `missing_tools`。安裝走既有 `POST /a/{slug}/items/{id}/skills/install`。

### 前端

- 列表頁、skill 頁各自的版面類別取代 `.page`；寬度為 CSS 變數（D5a）。
- skill 頁：頁首（名稱、說明、owner、管理 ▾）＋分頁籤（說明／檔案／版本紀錄／fork，`?tab=`）＋右側欄；
  390 時右側欄移到頁首下方。
- 檔案樹、diff、版本紀錄、安裝對話框、workspace 的 skill 面板依 D9–D14。

## Phases

每一步 `/tdd`：先寫在未修程式碼上變紅、走真路徑的測試；一個 phase 一個 commit。

- **P1** 後端列表：分頁、`total`、owner 篩選、`sort=updated`（`content_at`）、瀏覽只回原作＋`fork_count`。
- **P2** 後端 skill 頁：版號、檔案大小與 script 數、`/installs`、`/targets`。
- **P3** 前端列表頁（D1、D2、D4、D15、D16、D17 中列表的部分）。
- **P4** 前端 skill 頁骨架：頁首、管理 ▾、分頁籤、右側欄、安裝對話框、你裝在哪、錯誤畫面、下架確認（D3、D5、D6、D10、D16）。
- **P5** 檔案分頁（D12）。
- **P6** 版本紀錄與比對（D8、D13、D14）。
- **P7** workspace 的 skill 面板、安裝頁、對話卡片、發布結果卡（D9、D15）。
- **P8** 用詞（D7、D11）與其餘小修（D17）。
- **P9** `docs/migrations.md`（列表行為改變、`content_at` 不回填）、`contract.md`。
- 之後：**web demo** 與使用者一起看（D5a 寬度在此定），再進 review 與 CI。

## 驗證

- 每個 phase：後端 route 測試、前端元件測試；新守衛用突變驗證。
- 量級情境用盤點時的資料（985 檔、約 207 筆、40 版、20 fork、15 工具）在真 Chromium 1280／390 量頁高與
  `scrollWidth`/`clientWidth`。
- web demo 錄影給使用者看過，寬度依使用者意見調整。
