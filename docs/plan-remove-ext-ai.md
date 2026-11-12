# 拿掉「問外部AI」(`ask_ext_ai`)

**狀態:** P1–P4 完成;P5(每個目標的長度上限)進行中(#1021);施工中定的細節在 §7。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master` 的程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻。

## 1. 為什麼

`ask_ext_ai`(#963,`docs/plan-ext-ai.md`)讓小幫手去問公司的網頁 AI。它預設關著(`server.ext_ai_enabled`),
依目前所知沒有部署打開過,使用者也不用它:「請幫我查」(`ask_outside`)已經涵蓋——AI 出卡片,人自己去問、把回答貼回來
〔user〕「我們沒有那個需求因為ask outside就cover掉了」。

問 AI 和 Google 搜尋本質上是同一件事:一段文字交給一個目標,帶回結果。「本來就是 我只是不要你分兩個一樣的功能拆兩個工具」
〔user〕。後端與卡片看小幫手是黑盒子——交給它一段填好的網址,它交回一份結果或失敗原因;某種網址要怎麼「問」一個網頁 AI,
是小幫手裡的事,而且是公司內部的事〔user〕「你不需要對小幫手有什麼改變 把沒在用的askai刪掉 問ai這件事情我們自己會做
你根本幫不上忙因為那是公司內部的事情」。

所以這個 PR 做兩件事:拿掉多出來的 `ask_ext_ai`;讓一個搜尋目標本身能收一整句問題——每個目標自己的查詢長度上限
〔user〕「要」。(中間一度說成「合併的設計整個作廢、維持共用上限」〔user〕「對」,後來 user 說明本意,改回來。)

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| R1 | **整個拿掉 `ask_ext_ai`**:工具、卡片、小幫手的差事、網站檔範本、`server.ext_ai_enabled`、測試、live check、文件段落。當它從沒出現過 | 〔user〕 |
| R2 | **一種能力一個工具**:問 AI 不是另一個工具,是「請幫我查」的一個目標 | 〔user〕「我只是不要你分兩個一樣的功能拆兩個工具」 |
| R3 | **每個目標自己的查詢長度上限**:`max_words`(用空白分的詞)、`max_cjk`(中日韓字),兩個都要符合;沒寫 = 10/40(現在的規則);`0` = 那一把尺不限。AI 的關鍵字照**它建議的目標**的上限檢查,沒建議(或建議了不存在的)就照清單第一個——卡片預設選的那個。AI 的工具說明寫出每個目標的上限 | 〔user〕「對」(每個目標自己設)、「好吧 那還是保留兩種設定」、「要」 |
| R4 | 舊資料照樣讀得出來,只是不再產生:已存的小幫手工作若是 `ask_ext_ai`,`kind` 仍可解碼(不從 `JobKind` 拿掉),但不會再有新的,也沒有小幫手會接 | 〔施工〕 |
| R5 | 聊天紀錄裡舊的「問外部AI」卡片,拿掉之後顯示成一般的工具紀錄(不再畫成卡片);不另寫轉換 | 〔施工〕 |
| R6 | **小幫手不為「問 AI」做任何事**;它的網站檔機制照 R1 拿掉,某種網址怎麼問網頁 AI 由公司自己在小幫手裡做 | 〔user〕 |

## 3. 要拿掉的東西〔查證〕

- **後端**:`agent/ext_ai.py`(工具與授權規則);`litellm_runner._agent_for` 加它的那一段;
  `AgentToolContext.ext_ai`、`turn_context`、`health/replay.py`、`health_routes` 一路傳的 `ext_ai`;
  `create_app(ext_ai_enabled=)` 與 `__main__` 的接線;`ServerSettings.ext_ai_enabled`。
- **卡片格式**:`agent/outside_lookup.py` 的 `{"why", "question"}` 這種卡片(`_CARD_KEYS`、`declared_lookup`)、
  前端 `renderers/outsideLookup.ts` 的同一份讀法、共用對照表 `tests/fixtures/outside_lookup_cases.json` 裡的
  question 例子。
- **交給小幫手**:`outside_job_routes.py` 的 `"question"` 分支、`ask_ext_ai` 的檢查與錯誤訊息、
  `HeartbeatIn.ext_ai`、狀態回應裡的 `ext_ai`;`outside_jobs.py` 的 `_AiWorkspaceHelperPresence.ext_ai` 與
  `can_ask_ext_ai`;`outside_lookup_routes.py` 存檔時的 `_ext_ai_document`。
- **小幫手**:`ai_workspace_helper/ext_ai.py`、`builtin_errands.build(site=…)`、`app.py` 載入網站檔與心跳回報
  `ext_ai`;`ai-workspace-helper/ext_ai_site.template.py`;`scripts/pack_ai_workspace_helper.py` 的
  `--ext-ai-site`;`scripts/python39_guard.py` 對網站檔的說明;`.gitignore` 的網站檔條目。
- **前端**:`OutsideLookupCard.tsx` 的 question 分支與「問外部AI」字樣;`AgentEntryView.tsx` 的
  `ask_ext_ai` 對應;`i18n.tsx` 的 `lookup.ext*`、`tool.ask_ext_ai`;`skill-hub.css` 只有它用的樣式。
- **其他**:`apps/subagents.py` 不給子代理的工具清單裡的 `ask_ext_ai`。
- **測試與腳本**:`tests/agent/test_ask_ext_ai.py`、`tests/api/test_ext_ai_config.py`、
  `tests/api/test_ext_ai_jobs.py`、`tests/ai_workspace_helper/test_ext_ai.py`、
  `tests/ai_workspace_helper/test_ext_ai_in_chrome.py`、`web/src/components/OutsideLookupCard.extAi.test.tsx`、
  `scripts/check_ext_ai.py`;其他測試檔裡提到它的部分(`test_tool_grant_doors`、`test_replay_api`、
  `test_outside_jobs`、`test_worker`、`test_app`、`test_pack`、`test_python39`、`AgentEntryView.test.tsx`、
  `OutsideLookupCard.helper.test.tsx`)。
- **文件**:`docs/configuration.md`、`docs/ai-workspace-helper.md` 講它的段落;`plan-ext-ai.md` 標
  `> 被 #1021（plan-remove-ext-ai.md）推翻`;`design-history.md` 加這份計畫的一行。舊計畫
  (plan-outside-lookup、plan-outside-helper、plan-lookup-target-choice)與 `migrations.md` 過去的條目是歷史,不改。

## 4. 相容性

- **設定**:設定檔裡還寫著 `server.ext_ai_enabled` 的部署,升級後因為「不認得的設定」開不了機——loader 拒絕
  不認得的 key。所以 `migrations.md` 要寫:rollout 前把那一行刪掉。
- **小幫手**:舊版小幫手的心跳多帶 `ext_ai` 欄位,後端不認得就忽略(`HeartbeatIn` 是一般的 pydantic 模型),
  不會被拒;舊版小幫手也不會再收到 `ask_ext_ai` 的工作。所以協定號不必升〔施工,實作時用測試確認〕。
  小幫手照常重新打包,新版就沒有這個功能。
- **已存的資料**:見 R4、R5。

## 5. 推翻

- `docs/plan-ext-ai.md` 全部(D1–D12)。

## 6. 階段

| # | 內容 | 驗收 |
|---|---|---|
| P1 | 後端:工具、授權、`ext_ai_enabled`、一路傳的 `ext_ai`、卡片的 question 格式、交給小幫手的分支、存檔 | AI 的工具裡沒有 `ask_ext_ai`;設定寫 `ext_ai_enabled` 開機失敗並說出是哪個 key;舊的 `ask_ext_ai` 工作讀得出來 |
| P2 | 小幫手:差事、網站檔載入、心跳的 `ext_ai`、打包參數、範本 | 舊版小幫手的心跳(帶 `ext_ai`)仍被接受 |
| P3 | 前端:卡片的 question 分支、工具名稱、字樣、樣式 | 舊的「問外部AI」紀錄顯示成一般工具紀錄,不壞 |
| P4 | 測試、腳本、文件:刪掉只為它存在的,改掉提到它的;`migrations.md` 條目 | 全 repo 搜不到 `ext_ai`(歷史計畫與 migrations 舊條目除外) |
| P5 | 每個目標的長度上限(R3):loader 收 `max_words`/`max_cjk`、`whole_lookup_targets` 補預設、`ask_outside` 照目標檢查、工具說明寫出上限、文件 | 沒寫的目標照舊 10/40;AI 目標設 0 收得下整句;建議 A 目標時照 A 的上限 |
| P6 | web-demo → review(conformance / veracity / defect / regression)→ CI | 照 CLAUDE.md |

## 7. 施工中定的細節

- **C1** `.gitignore` 裡公司網站檔(`ext_ai_site.py`)的忽略規則**保留**,只改註解——§3 原本寫拿掉。一台電腦上
  留著的那份檔案裡有公司自己的網址,拿掉規則,下一次 `git add` 就可能把它 commit 上去;`test_pack` 裡「網站檔
  永遠不被 commit」的測試跟著留下〔施工〕。
- **C2** P1 和 P2 一起 commit:後端和小幫手共用心跳/狀態的約定(`HeartbeatIn`、`/ai-workspace-helper/status`),
  只改一邊,另一邊的測試就紅〔施工〕。
- **C3** 小幫手工作的 `note` 不再有 `login`(那是網站檔等人登入外部 AI 時才設的),只剩 `captcha`〔施工〕。
- **C4** 舊的 `ask_ext_ai` 紀錄在聊天裡顯示成一般工具紀錄,標籤是通用的「使用工具」——沒對應的工具一律這樣顯示,
  原始的工具名稱不上畫面(#160)〔施工〕。
