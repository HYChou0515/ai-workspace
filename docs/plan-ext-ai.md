# 問外部AI:AI 透過小幫手去問公司的網頁 AI

**狀態:** 施工中。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master` 程式碼或文件確認的事實;
〔施工〕= 我定的實作細節,可推翻;〔指南〕= 出自公開的 UI/UX 指南,附出處。

## 1. 問題

公司有一個能上外網的 AI,**只有網頁介面**〔user〕「他是一個純網頁的介面」。後端斷網,我們的 AI 問不到它。
那個網頁是公司的資產,我們拿不到網址、也看不到長相〔user〕「我也不能給你網址 那是公司的資產」;
打開某個網址就是一個空白對話〔user〕「從某網址直接到空白對畫框的網頁」;不能把問題放進網址,只能
「真的去輸入對話按送出 等他跑完在copy貼回」〔user〕。

已經有的零件〔查證〕:

- 「請幫我查」卡片(`ask_outside`,plan-outside-lookup):AI 畫卡片、turn 停下、人的回答是下一則訊息;回答存成
  `lookups/` 的檔案(`api/outside_lookup_routes.py:_save`),同一個 `deliver` 也收小幫手抓回的頁面。
- 小幫手(plan-outside-helper):使用者電腦上的 Python,用 Playwright 開使用者自己的 Chrome。差事(errand)是
  `(ctx, args) -> FetchedPage` 的函式,依 job 的 `kind` 註冊(`errands.py`:「A new errand is a new function」);
  `ErrandContext` 已有等人過驗證(`wait_for_person`,D25)、轉 Markdown(`convert`,D11)、等頁面穩定(`settle`,D22)。
- 伺服器從不送程式碼給小幫手執行;更新只通知、不自動安裝(plan-outside-helper D21,#933)。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 工具叫 **`ask_ext_ai`**。公司 AI 是**顧問**:大腦仍是我們的模型,需要外部資訊或別人的分析時呼叫它,拿回答繼續做事——不是換掉大腦(網頁沒有 tool calling、每輪都要開瀏覽器、每輪都把整段對話送出去) | 〔user〕「ask_ext_ai」「Q1: a」 |
| D2 | **每次送出前都要人按「問外部AI」**。問題由 AI 起草,卡片完整顯示、可以改;送出去的是框裡的字。回答回來直接交給 AI,不再按一次(同 plan-outside-helper D4) | 〔user〕「問 改成 問外部AI」 |
| D3 | **只送文字**,不上傳檔案;要附的資料由 AI 摘進問題 | 〔user〕「文字」 |
| D4 | **每次都打開那個網址**,得到全新的空白對話;停在登入頁就等人登入(同 D25 的等法) | 〔user〕「可以 從某網址直接到空白對畫框的網頁」 |
| D5 | 回答存成 `lookups/` 的檔案(問題+回答),不能加檔的人只送訊息;**只在聊天裡給**(同 plan-outside-lookup D6);部署開關 `server.ext_ai.enabled`,**預設關** | 〔施工〕 |
| D6 | **分工**:`ask_outside` 拿**原文**(一頁文件、搜尋結果);`ask_ext_ai` 拿**分析**(比較、解釋、整理)。兩個工具的說明都寫明;引用 `ask_ext_ai` 的回答要說明是外部AI 的說法 | 〔施工〕(user:「你把模子刻好…我希望我們碰的都是你無法知道的」) |
| D7 | **交界是一個 Python 檔**:公司寫 `ext_ai_site.py`——`URL` 加一個 `ask(page, question, h) -> str`(回答的 HTML 或文字),拿到整個 Playwright 頁面、怎麼操作都行。**不是**伺服器設定裡的幾個 selector:那等於先假設網頁有一個輸入框、一個送出鈕、一個回答區,沒人能保證。打包時 `--ext-ai-site` 把它放進小幫手 | 〔user〕「不太好 因為這很大的假設了我們的網頁長相 我不清楚是不是夠用」「Ok」 |
| D8 | **按鈕字樣:手動和小幫手一字不差**。搜尋:「**{name} search**」(手動多一個 ↗);開網址:「**開啟這個網址**」;失敗重來:「**再 {name} search 一次**」/「**再開一次**」/「**再問外部AI 一次**」。`{name}` 是小幫手會用的那個目的地(`pick_search_target`:Google,沒有就第一個),卡片和小幫手用同一條規則 | 〔user〕「查 改成 Google search (手動和自動 wording上不應該有任何區別）」「對」 |
| D9 | 卡片要知道**這個人的小幫手能不能問外部AI**:小幫手報到時多報 `ext_ai`(打包進來的網站檔名稱;沒有就空)。能 → 「問外部AI」;不能(沒連線、舊版、沒網站檔)→ 手動流程:「複製問題」,自己去問,把回答貼回卡片 | 〔施工〕 |
| D10 | 給 AI 的訊息開頭是「我問了外部AI:…」,並說明**這是外部AI 的回答,不是原始出處**;回答裡的連結照 D24 編號列在檔案最下面,要看原文用 `ask_outside` 開 | 〔施工〕(D6;〔指南〕NN/g Explainable AI) |
| D11 | **等多久**:整件事上限 `EXT_AI_TIMEOUT_S`(300 秒,和等人過驗證的 `VERIFY_TIMEOUT_S` 同一個數)。網站檔的長時間等待要經過 `h`(`h.wait_until` / `h.settle`):它們會續約這個 job(否則 120 秒沒續約,job 會被當成沒人做,再被領一次=**問兩次**),也會在超時時停下 | 〔施工〕(〔查證〕`CLAIM_TIMEOUT_MS`、`RENEW_S`) |
| D12 | 網站檔**只從打包進來**,伺服器設定裡沒有網址、沒有程式碼;小幫手送回結果時**不帶網址**(`url=""`)——公司的網址只出現在公司自己的檔案裡 | 〔施工〕(D7;plan-outside-helper D21) |

## 3. 卡片

和「請幫我查」同一張卡片(`OutsideLookupCard`),多一種:`{why, question}`。

- **標題**「問外部AI」,下一行是 AI 寫的 why。
- **「要問的問題」**:多行文字框,預先填好 AI 起草的問題,可以改(D2)。〔指南〕NN/g「Confirmation Dialogs Can Prevent
  User Errors — If Not Overused」:送到外部服務收不回來,所以送出前讓人看得到、改得動;但「Do not use confirmation
  dialogs for routine actions」——按鈕本身寫出結果,不再加第二次「確定嗎」。框空了就不送,說「先在框裡寫好要問的問題」
  (同 D13 的做法)。
- **主要按鈕「問外部AI」**(小幫手能問時,D9)。〔指南〕NN/g「UI Copy」:「Lead with verbs or verb phrases that clearly
  outline what will happen」;GOV.UK Button:「describing the action it performs」。
- **等待中**:「小幫手正在問外部AI…(已 N 秒)」+「取消」。〔指南〕NN/g「Progress Indicators Make a Slow System Less
  Insufferable」:超過 10 秒不要只放轉圈動畫,要「give users the option to stop the process」;NN/g「Status Tracker」:
  「provide regular updates, even if they are of low granularity」——秒數就是那個更新。狀態文字 `role="status"`(polite),
  失敗才用 `role="alert"`(UK Home Office Design System,Notifications)。
- **失敗**:原因 +「再問外部AI 一次」,框裡的問題保留。〔指南〕NN/g Error-Message Guidelines:「Let users correct errors by
  editing their original action instead of starting over」;按鈕再說一次動作名,是由 NN/g UI Copy 與一致性原則推得,沒有
  直接講這件事的出處。
- **手動**(D9 不能問時,或按「自己問」):「複製問題」(同 D13 的複製鈕:寫進剪貼簿才說「已複製」),把回答貼進
  「外部AI 的回答」,「送出」;「沒問到／不問了」。
- **字樣一致**(D8):〔指南〕NN/g 十大原則 #4:「Users should not have to wonder whether different words, situations, or
  actions mean the same thing」;NN/g UI Copy:同一個指令出現在幾個地方,「use the same label text in each instance」。

## 4. 小幫手

- **差事 `ask_ext_ai`**(`builtin_errands.build`):有網站檔才註冊。開新分頁 → 呼叫網站檔的 `ask(page, question, h)` →
  回傳的 HTML 用卡片的轉換器(D11)轉成 Markdown → `FetchedPage(title="外部AI", url="", markdown)`。
- **`h`(模子提供給網站檔的幫手)**:
  - `h.wait_for_login(ready)`:`ready()` 不成立(還在登入頁)就把分頁帶到前面、卡片顯示「請在小幫手的 Chrome 裡登入」,
    等到成立(同 D25 的 `wait_for_person`)。
  - `h.wait_until(cond, what)`:等 `cond()` 成立,期間續約、超過 D11 上限就失敗,失敗訊息說在等什麼。
  - `h.settle(page)`:等頁面不再變動(D22)。
- **範本** `ai-workspace-helper/ext_ai_site.template.py`:用通用推斷寫好的 `ask`——找最大的可見輸入框(`textarea` /
  `contenteditable`)、貼上、按 Enter、等頁面停止變動、取送出之後新出現的那一塊。公司從它改起。用一個假的聊天網頁
  (會一個字一個字吐回答、有登入頁)測過。
- **試跑** `python -m ai_workspace_helper try-ext-ai --site <檔> "問題"`:在使用者自己的電腦上對真的網站跑一次,每一步和
  回答印在自己的畫面上,不經過伺服器。
- **打包** `scripts/pack_ai_workspace_helper.py --ext-ai-site <檔>`:把網站檔放進小幫手的套件(`ai_workspace_helper/
  ext_ai_site.py`)。網站檔改了要重新打包、大家重裝。

## 5. 分期

| 期 | 內容 | 驗收 |
|---|---|---|
| P1 | 後端:`ask_ext_ai` 工具、卡片 `{why, question}`、授權規則(聊天 + `ask_user` + `server.ext_ai.enabled`)、交給小幫手(`kind="ask_ext_ai"`,送框裡的字,空的 422)、回答存檔與訊息(D10)、報到多 `ext_ai` | 單元測試;開關關著就沒有這個工具 |
| P2 | 卡片:第三種卡片(§3)、D8 字樣 | 前端測試;截圖對照 §3 引用 |
| P3 | 小幫手:差事、`h`、載入網站檔、範本、假網站測試 | 假網站上從按「問外部AI」到 AI 拿到回答整條跑通 |
| P4 | `try-ext-ai`、打包 `--ext-ai-site` | 打包出的 wheel 裡有網站檔;試跑印出每一步 |
| P5 | 文件:`configuration.md`、`ai-workspace-helper.md`(公司要做的事)、`migrations.md`;live check + 截圖 | — |

## 6. 公司要做的事(運營方)

1. 複製 `ai-workspace-helper/ext_ai_site.template.py` 成自己的 `ext_ai_site.py`,填 `URL`。
2. 在自己的電腦上 `try-ext-ai` 試跑;哪一步不對,就改 `ask` 裡那幾行。
3. `pack_ai_workspace_helper.py --version <新版> --ext-ai-site ext_ai_site.py`,放到 `server.ai_workspace_helper_dir`。
4. 設 `server.ext_ai.enabled: true`,重新部署;大家重裝小幫手。
