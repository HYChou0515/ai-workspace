# 設計計畫與歷史

這裡收錄的是**設計演進的歷史紀錄**：每個功能在動工前的 grill-me / plan 文件、被否決的替代方案、以及給接手者的 handoff。它們**不是**目前架構的權威說明——權威說明請看 [系統架構](architecture.md)、[線上契約](contract.md) 與 [開發者指南](development.md)。

> 寫新的 plan：在這裡的對應段落加一行；它若推翻了某份舊 plan，在那份舊 plan 的標題下加上推翻它的 id——規則見專案 `CLAUDE.md` 的 Workflow 段（「A plan that overturns an earlier plan marks it」）。app 裡的 AI 從這份索引找 plan，沒列在這裡的它讀不到。

> 為什麼留著？這些文件記錄了「**為什麼這樣設計、當初考慮過哪些路、為什麼不走**」。當你想改某個決策時，先回來看看它原本被否決的理由，通常能省下重踩一次坑的時間。

---

## 平台與 App 模板

| 文件 | 主題 |
|---|---|
| [plan-app-templates.md](plan-app-templates.md) | #89 RCA → 多 App 平台：`apps/<slug>/` 模板、WorkItemBase、三層 agent resolve |
| [plan-backend.md](plan-backend.md) | 最初的後端設計計畫（分層 / Protocol / SSE 的原始脈絡） |
| [plan-frontend.md](plan-frontend.md) | 最初的前端設計計畫（VSCode 風格 UI） |
| [plan-topic-hub.md](plan-topic-hub.md) | Topic Hub App：跨 collection 探究工作區、檔案記憶、多 chat |
| [plan-item-memory.md](plan-item-memory.md) | item 記憶機制（仿 Claude Code memory）：記憶即 workspace 檔案、agent 在 turn 中自寫、全 App 預設開 |
| [plan-collab-workspace.md](plan-collab-workspace.md) | #43 多人協作 workspace（檔案 + chat，無 notebook） |
| [plan-permissions.md](plan-permissions.md) · [plan-permissions-pr2-handoff.md](plan-permissions-pr2-handoff.md) | 權限模型設計與分階段交接 |
| [plan-view-plugins.md](plan-view-plugins.md) · [PR1](plan-view-plugins-pr1-platform.md) · [PR2](plan-view-plugins-pr2-chart.md) · [PR3](plan-view-plugins-pr3-marking-layout.md) · [PR4](plan-view-plugins-pr4-stack-gallery.md) | #847 + #848:`*.ai.yaml` renderer 改成 runtime plugin(前端 + 沙盒 bundle + skill 一個資料夾)、chart plugin 當範例、有名字的 marking 跨 workspace 切版連動、疊圖 / 差異 / 上千組縮圖牆(沙盒端快取 + 分頁);機制零領域知識 |
| [plan-ai-reads-docs.md](plan-ai-reads-docs.md) | app 裡的 AI 讀我們的 docs(含沒被推翻的計畫):readonly skill 每次 `read_skill` 比對 hash、不同就重複製;子 agent 先讀索引再挑檔;被推翻的計畫帶上推翻它的計畫 id |
| [plan-ai-sheet.md](plan-ai-sheet.md) | `*.ai.csv` 試算表編輯：格子直接打字、增刪列欄、虛擬化、範圍選取與 TSV 剪貼簿（Excel 互通）、undo/redo；單一檔案零 sidecar，後端零知識；附「為何不用 xlsx」實測 |
| [plan-external-handoff.md](plan-external-handoff.md) | 外部系統把工作交進 App 的 work item（不引入新概念） |
| [plan-delete-item-cascade.md](plan-delete-item-cascade.md) | 刪除 item 時一併刪除它擁有的東西 |
| [plan-issue-308.md](plan-issue-308.md) | #308 單份文件的權限覆寫 |
| [plan-issue-608.md](plan-issue-608.md) | #608 依 group（組織層級的群組）設定權限 |
| [plan-issue-331.md](plan-issue-331.md) | #331 topic-hub 在 step 之外沒有詳細進度 |
| [plan-issue-748.md](plan-issue-748.md) | #748 一則回答是誰、什麼時候、花多少 |
| [plan-subagent-model-choice.md](plan-subagent-model-choice.md) | `run_agent` 的呼叫方替子 agent 選模型 |
| [plan-verify-number.md](plan-verify-number.md) | `verify-number` skill：交出可以查核的數字 |
| [plan-third-party-tools.md](plan-third-party-tools.md) | 第三方 tool 散布：作者跑自己的 CI，新 sandbox 自動帶上 |
| [plan-tools-picker-groups.md](plan-tools-picker-groups.md) | 工具挑選器依套件摺疊、內建工具一組、每組三態 |
| [plan-user-env-exec-injection.md](plan-user-env-exec-injection.md) | 使用者環境變數改由 exec 注入（取代 `.userenv` 檔） |
| [plan-view-plugins-pr5-finish.md](plan-view-plugins-pr5-finish.md) | #847 / #848 在 #855 分支上的收尾（PR5） |
| [plan-marking-tuples.md](plan-marking-tuples.md) | #861 marking 記住選到的整組 key（D1–D6） |

## 知識庫（KB）與檢索

| 文件 | 主題 |
|---|---|
| [plan-kb-parsers.md](plan-kb-parsers.md) | #39 KB 解析器：parser 吐整檔 Document、splitter 掌管切塊粒度 |
| [plan-llamaindex-ingest.md](plan-llamaindex-ingest.md) | 以 LlamaIndex 重構攝取管線 |
| [plan-kb-retrieval-enhancements.md](plan-kb-retrieval-enhancements.md) | multi-query / HyDE / rerank 的 enhancement 旋鈕設計 |
| [plan-rag-context.md](plan-rag-context.md) | RAG 前後文四階段：中文切段修正（`\S+` 讓中文整份一塊）、自動字元窗擴展（可跨檔案、樹序、rerank 之前、citation 留在命中處）、限定文件/資料夾的語意與精確關鍵字搜尋、分頁讀圖 + 分行讀文字 |
| [plan-retrieval-llm-refactor.md](plan-retrieval-llm-refactor.md) | 檢索 LLM 介面重構 |
| [plan-llm-wiki.md](plan-llm-wiki.md) | #50 LLM wiki：與 chunk-RAG 平行的第二條維基管線 |
| [plan-context-cards.md](plan-context-cards.md) | #106 Context cards：輕量、確定性的詞彙卡（glossary） |
| [plan-collab-kb.md](plan-collab-kb.md) | KB 協作設計 |
| [plan-issue-230.md](plan-issue-230.md) | #230 介紹 / 說明頁 + AI 問答 |
| [plan-issue-281.md](plan-issue-281.md) | #281 讀程式碼的 AI 生成 wiki |
| [plan-issue-281-followup.md](plan-issue-281-followup.md) | #281 follow-up：code-wiki 補缺口與擴展 |
| [plan-issue-328.md](plan-issue-328.md) | #328 可找到性探測 modal（互動式 prompt 調校） |
| [plan-issue-328-followup.md](plan-issue-328-followup.md) | #328 follow-up：Tune parsing（單份文件的 prompt 逃生口 + 答案預覽） |
| [plan-issue-355.md](plan-issue-355.md) | #355 Web 表單建立 code collection、同步改成 job、每日 03:00 自動同步 |
| [plan-issue-377.md](plan-issue-377.md) | #377 AI 針對文件主動詢問不懂的地方 |
| [plan-issue-397.md](plan-issue-397.md) | #397 讓 AI 修改 wiki 的工具 + 即時指正 UX |
| [plan-issue-402.md](plan-issue-402.md) | #402 文件檔案樹篩選 + 可調寬度 |
| [plan-issue-506.md](plan-issue-506.md) | #506 文件提問 / context card 建議：效能、品質、閉環去重 |
| [plan-cardgen-drafter-wiki-suppression.md](plan-cardgen-drafter-wiki-suppression.md) | Drafter 用 wiki 自我壓卡（#506 / #577 follow-up） |
| [plan-issue-511.md](plan-issue-511.md) | #511 待審 inbox 真分頁：CardGen 提案抽成獨立 resource |
| [plan-issue-520.md](plan-issue-520.md) | #518 + #520 卡片連文件、檢索收斂、「圖片 → 知識」起手範本 |
| [plan-knowledge-graph-answers.md](plan-knowledge-graph-answers.md) | 知識圖譜的回答面（#534 → #628 / #630 / #633） |
| [plan-issue-624.md](plan-issue-624.md) | #624 context 上限：偵測、處理、告知 |
| [plan-context-limit-behind-proxy.md](plan-context-limit-behind-proxy.md) | 自架模型在 proxy 後面時解不出窗口上限 |
| [plan-issue-739.md](plan-issue-739.md) | #739 對話塞不下時壓成摘要，而不是叫使用者開新對話 |
| [plan-104-remove-source-doc-id.md](plan-104-remove-source-doc-id.md) | #104 收尾：chunk 綁內容、去除 `source_doc_id` 依賴、拔除 re-home |

## Workflows

| 文件 | 主題 |
|---|---|
| [plan-workflows.md](plan-workflows.md) | #100 API 觸發的 headless workflow：FS-as-journal、produce→review→commit |
| [workflows-frontend-brief.md](workflows-frontend-brief.md) | Workflows 前端設計 brief |
| [plan-make-deck-runtime-craft.md](plan-make-deck-runtime-craft.md) | #284 make_deck：意圖 → 多模態子代理迴圈產投影片 |
| [plan-issue-323.md](plan-issue-323.md) | #323 使用者自己寫的 workflow（DSL） |
| [plan-issue-343.md](plan-issue-343.md) | #343 在目前的對話裡啟動 workflow（接管） |
| [plan-issue-429.md](plan-issue-429.md) | #429 Workflow 引擎三個待補缺口（+ 雜項） |
| [plan-workflow-language-alignment.md](plan-workflow-language-alignment.md) | Workflow 可靠性：node contract、verify、authoring（維持 JSON DSL） |
| [plan-cache-required.md](plan-cache-required.md) | DSL 每一步都必須有 `cache`；解析不了的 workflow 到處都說清楚 |
| [plan-item-schedules.md](plan-item-schedules.md) | item 層級的排程：讓 AI 把自己做的 workflow 放上時鐘 |
| [plan-schedule-overview.md](plan-schedule-overview.md) | 排程總表：跨 item 列出看得到的排程，上次／下次、改時間、現在執行；新排程不補跑 |
| [plan-schedule-overview-polish.md](plan-schedule-overview-polish.md) | 排程總表整修：時間換成看的人的時區與相對說法、名稱用標題、手動執行標記、排程對話的橫幅 |
| [plan-schedule-cron.md](plan-schedule-cron.md) | cron 排程：一列寫完原本要拆成好幾列的排程；改時間有簡單／cron 兩種模式 |

## Sandbox 與基礎設施

| 文件 | 主題 |
|---|---|
| [plan-http-sandbox.md](plan-http-sandbox.md) | #60 HTTP sandbox host：把 sandbox 拆成獨立 HTTP 服務 |
| [plan-sandbox-sot.md](plan-sandbox-sot.md) | sandbox 真相來源（source-of-truth）設計 |
| [plan-sandbox-resource-quota.md](plan-sandbox-resource-quota.md) | 依 App 設定 sandbox 資源（cpu/memory/disk）+ 每人跨 App 總量上限；債務人是 item 的 `owner`（前提見 #687） |
| [plan-item-sandbox-resources.md](plan-item-sandbox-resources.md) | 每個 item 自己決定環境開多大（使用者可調、由 owner 的額度支出）；狀態／用量／關閉搬到 item 頁面；閘門是 `change_permission` |
| [plan-llm-failover.md](plan-llm-failover.md) | LLM failover / 多供應商備援 |
| [plan-sanity-checks.md](plan-sanity-checks.md) | 開機健康檢查 / sanity matrix |
| [plan-repetition-guard.md](plan-repetition-guard.md) | #113 重複迴圈偵測與優雅阻擋 |
| [plan-max-turns-continues-goal.md](plan-max-turns-continues-goal.md) | #721 撞到 `runner.max_turns` 不再被當成失敗——goal 的自動續跑改由「工具呼叫指紋」判斷是否在原地打轉,而不是「這輪結束得好不好」 |
| [plan-turn-replay-buffer.md](plan-turn-replay-buffer.md) | 同台重連無損：#43 broadcast 加 in-pod seq + ring buffer，`?since=` replay 斷線期間漏掉的事件 |
| [plan-event-bus-cross-pod-streaming.md](plan-event-bus-cross-pod-streaming.md) | 跨 pod live 串流：RabbitMQ fanout 事件匯流排（IEventBus，memory 預設 + rabbitmq），不再依賴 sticky |
| [plan-skills-and-tools.md](plan-skills-and-tools.md) | Skills 與 tools 套件設計 |
| [plan-skill-hub.md](plan-skill-hub.md) | skill hub：使用者之間分享 skill，不經過 dev 的版本庫——在 item 裡發布（結構擋、AI 審掛意見）、在 item 裡裝（副本 + `.origin`，告知目標 App 缺的 tool）、非 owner 只能 fork、owner 在詳情頁管理；十個 grill 決策的原文 |
| [plan-skill-hub-ui-polish.md](plan-skill-hub-ui-polish.md) | skill hub 的 UI 打磨（#826）：九段 demo 錄影抓到的 22 個問題，每一項對照教科書做法（Material 3 / Apple HIG / NN/g / Polaris / GOV.UK / MUI）決定 D1–D17——對話框預設留白、搜尋框不失焦、錯誤只報一次、成功提示、安裝前先標「已有同名 skill」、圖示各自專用＋tooltip、可見範圍對話框走 i18n、伺服器拒絕句改 code、工具回話分段、窄寬的頂欄與麵包屑；版面一律真 Chromium 量 1280／390 |
| [plan-skill-hub-history.md](plan-skill-hub-history.md) | skill hub 的歷史與回溯、下載/使用次數、問 AI 該裝哪個(grill 完成,P1–P10):一個 skill 一個 bare git repo(自己的 `skill_hub.git_root`)、`master` = 目前版本、push `--force-with-lease` 搶鎖、回復 = `master` 指回舊 commit、每個 revision 打 tag `r-<revision id>`、hub 副本 `.origin` 記 commit、LFS 以路徑模式、檔案數上限 1000;使用次數記憶體累積每 2 小時寫出;只在 item 聊天問 AI、`show_skill_hub_entry` 卡片 |
| [plan-chat-video.md](plan-chat-video.md) | **PR #817(script 版 P1–P5 已做)**。把一段對話紀錄做成影片：吃 `.chat.json`、一條指令、不打 LLM；純 HTML/CSS 的 zoom 推進輸入框、指定長寬；核心是純函式 + msgspec options，為之後的前端按鈕 + 獨立 job / worker pod 鋪路 |
| [plan-headless-env.md](plan-headless-env.md) | 沒有人按送出的 turn（item 排程、goal driver、event trigger、整條 workflow）也拿得到環境變數：`IRequestEnv` 多一個 `env_without_request(user_id, item_id)`，預設 `{}`，部署的 impl 決定回 service account 還是什麼都不給；平台仍不認得 service account 這個詞；改變 #714「workflow 整條不接」的定案並寫明為何原顧慮不再成立 |
| [plan-wui-viewer-login.md](plan-wui-viewer-login.md) | 看頁面的人用自己的登入：環境變數分 shared（item 上，不動）與 private（每人每 item 一份，只有本人讀得到）兩層，每個 key 一種政策（`shared > private` 預設=今天的行為 / `private > shared` / `private only`）；callTool、聊天、頁面起的 run 用按的人，排程要本人按「用我的身分執行」才綁定、內容一改就失效；Env 面板分「只有我／所有參與者」兩分頁、tool 下拉換成可收合分段；`/w/` 在 iframe 上方加平台列（line of death） |
| [plan-tool-env-declaration.md](plan-tool-env-declaration.md) | #750 tool 宣告自己要哪些環境變數 + 第二方帳密換 env：手寫選填宣告跟著 package 走、沒宣告≠不需要、便民工具不是閘門；附「為何 tool 不指名方法」 |
| [plan-profile-python-env.md](plan-profile-python-env.md) | profile 自帶 python 環境:起始 `pyproject.toml`+`uv.lock` 決定 sandbox 套件,**每輪對話**跑一次 `uv sync --frozen --inexact`(`--inexact` 才不會刪掉使用者自己裝的);附「為何 venv 不放 workspace」「共用 cache 為何必須唯讀(hardlink 別名)」與十條被實作/review 推翻的原始宣稱 |
| [plan-sci-plot.md](plan-sci-plot.md) | #285 sci-plot 科學繪圖工具 |
| [plan-read-image.md](plan-read-image.md) | #112 read_image：VLM-over-workspace-image 工具 |
| [plan-code-qa.md](plan-code-qa.md) | 程式碼 QA 設計 |
| [plan-issue-366.md](plan-issue-366.md) | #366 sandbox 位址一致性（http sandbox-host） |
| [plan-issue-504.md](plan-issue-504.md) | #504 隔離 sandbox 的檔案 owner 不對 |
| [plan-idle-reap-holds-the-handle.md](plan-idle-reap-holds-the-handle.md) | sandbox handle 永遠能從 registry 找到 |
| [plan-sandbox-recovery-and-error-honesty.md](plan-sandbox-recovery-and-error-honesty.md) | 沙盒被收走之後平台不復原，還說錯原因 |
| [plan-issue-830.md](plan-issue-830.md) | #830 沙盒大小的硬上限由 record 帶給前端 |
| [plan-sandbox-modal-redo.md](plan-sandbox-modal-redo.md) | item 的 Sandbox modal 用現有元件重畫 |
| [plan-lazy-file-tree.md](plan-lazy-file-tree.md) | 檔案樹懶載入：打開 item 不該等 50 秒 |
| [plan-entity-listing-op-count.md](plan-entity-listing-op-count.md) | 列表變慢的真因：一次請求做了幾百次檔案操作 |
| [plan-api-lifecycle-offload.md](plan-api-lifecycle-offload.md) | 把 API lifecycle 裡不是 pod-local 的工作搬離 API pod（PR #804） |
| [plan-run-consumers-list.md](plan-run-consumers-list.md) | `server.run_consumers` 接受清單：all-in-one 行程只消費部分 JobType |
| [plan-graceful-shutdown.md](plan-graceful-shutdown.md) | pod 離開時不能帶走使用者的對話（graceful shutdown、turn 換 pod） |
| [plan-blob-gc-job.md](plan-blob-gc-job.md) | Blob GC 改成 job：API 只負責提出要求 |
| [plan-stop-reliability.md](plan-stop-reliability.md) | Stop 的可靠性 |
| [plan-archive-pack.md](plan-archive-pack.md) | 回收時把備份多存成一個 `<item>.pack.<gen>-<bytes>.tar`，再開時 NFS 只讀 1 次不讀 89k 次；樹仍是真相、名字帶 gen 自然作廢 |
| [plan-tool-running-version.md](plan-tool-running-version.md) | 第三方工具「沙盒裡實際在跑哪一版」：建立沙盒時把掛的 `{sha, version}` 寫進共用位址列；AI 的工具說明與工具選單改說實際版本，不一致時只告知、選單一顆「關閉沙盒以更新」；查不到當最新 |

## 前端與介面

| 文件 | 主題 |
|---|---|
| [plan-issue-460.md](plan-issue-460.md) | #460 前端缺陷批次 + #105 品質分數顯示缺口 |
| [plan-issue-680.md](plan-issue-680.md) | #680 entity 詳情 modal：三個 view 雙擊開啟 |
| [plan-issue-779.md](plan-issue-779.md) | #779 modal 誤關：離開 modal 只有一套規則 |
| [plan-input-class-sweep.md](plan-input-class-sweep.md) | #829 每個文字控制項都套 `.input` |
| [plan-scrollbars.md](plan-scrollbars.md) | 該捲的地方要捲，該一致的地方要一致 |
| [plan-datetimerange-role.md](plan-datetimerange-role.md) | `datetimerange` 這個 role 名稱已經在說謊 |
| [plan-chat-rail-manifest-nouns.md](plan-chat-rail-manifest-nouns.md) | chat rail 一律說「chat」、重複平台選單、藏起自己的下拉 |
| [plan-rail-menu-icons.md](plan-rail-menu-icons.md) | chat rail 的 ☰ 選單畫和全域切換器一樣的 icon |
| [plan-export-current-chat.md](plan-export-current-chat.md) | 匯出下載的是第一個 chat，不是正在看的那一個 |
| [plan-chat-video-export.md](plan-chat-video-export.md) | 前端匯出對話：文字（JSON / Markdown）與影片（job） |
| [plan-marp-render.md](plan-marp-render.md) | 在 workspace 檔案預覽裡渲染 Marp 簡報 |
| [plan-onboarding-images.md](plan-onboarding-images.md) | onboarding 內文走 markdown、可以放圖 |
| [plan-show-file-in-chat.md](plan-show-file-in-chat.md) | agent 在 chat 中顯示 workspace 的檔案（`show_file`） |
| [plan-chat-column-vertical-space.md](plan-chat-column-vertical-space.md) | 把聊天欄的高度還回來 |

## PM App

| 文件 | 主題 |
|---|---|
| [plan-pm-github-projects.md](plan-pm-github-projects.md) | 讓 PM app 用起來像 GitHub Projects |
| [plan-pm-ui-review.md](plan-pm-ui-review.md) | PM app UI review（PR #640 與後續） |
| [plan-pm-view-ordering.md](plan-pm-view-ordering.md) | PM view 排序 + View settings 面板（GitHub Projects 風格） |
| [plan-pm-entity-body-edit.md](plan-pm-entity-body-edit.md) | 使用者改不了 entity 內文、看板卡片藏了編號 |
| [plan-pm-gantt-urgency-and-axis.md](plan-pm-gantt-urgency-and-axis.md) | 甘特圖：緊急程度、上色來源、收合、以週為主的時間軸 |
| [plan-pm-auto-schedule.md](plan-pm-auto-schedule.md) | Timeline 上的自動排程 |
| [plan-issue-785.md](plan-issue-785.md) | #785 PM app 七項：時間軸到小時、非工時摺疊、一張會說謊的甘特圖 |

## WUI

| 文件 | 主題 |
|---|---|
| [plan-wui.md](plan-wui.md) | WUI：資料夾當可互動的網頁 |
| [plan-wui-deploy.md](plan-wui-deploy.md) | WUI「Deploy」：一個直接落在頁面上的 URL |
| [plan-wui-overview.md](plan-wui-overview.md) | WUI 總覽：列出所有已部署 WUI 的一頁 |
| [plan-wui-overview-icon-favourites.md](plan-wui-overview-icon-favourites.md) | WUI 總覽：頁面自己的 icon 與瀏覽者的最愛 |

## 各 issue 的計畫

逐 issue 的小型計畫文件（grill-me 鎖定決策 + flat phase 拆解）：

[plan-issue-93](plan-issue-93.md) ·
[105](plan-issue-105.md) ·
[132](plan-issue-132-multichat-ux.md) ·
[177](plan-issue-177.md) ·
[178](plan-issue-178.md) ·
[219](plan-issue-219.md) ·
[226](plan-issue-226.md) ·
[227](plan-issue-227.md) ·
[231](plan-issue-231.md) ·
[245](plan-issue-245.md) ·
[247](plan-issue-247.md) ·
[254](plan-issue-254.md) ·
[263](plan-issue-263.md) ·
[271](plan-issue-271.md) ·
[280](plan-issue-280.md) ·
[283](plan-issue-283.md) ·
[284](plan-issue-284.md) ·
[287](plan-issue-287.md) ·
[288](plan-issue-288.md) ·
[298](plan-issue-298.md) ·
[419](plan-issue-419.md) ·
[435](plan-issue-435.md) ·
[448](plan-issue-448.md) ·
[455](plan-issue-455.md) ·
[479](plan-issue-479.md) ·
[492](plan-issue-492.md) ·
[501](plan-issue-501.md) ·
[513](plan-issue-513.md)

彙整型：[plan-issues.md](plan-issues.md) · [plan-followups.md](plan-followups.md)

## 前端 handoff 與設計交接

| 文件 | 主題 |
|---|---|
| [fe-kickoff.md](fe-kickoff.md) | 前端開工說明 |
| [fe-blocking-gaps.md](fe-blocking-gaps.md) | 前端阻擋性缺口盤點 |
| [handoff-launcher-design.md](handoff-launcher-design.md) | Launcher 設計交接 |
| [handoff-wiki-fe-design.md](handoff-wiki-fe-design.md) | Wiki 前端設計交接 |

## 給 specstar 框架的問題

| 文件 | 主題 |
|---|---|
| [q-specstar-efficient-aggregates.md](q-specstar-efficient-aggregates.md) | 如何不 materialise 整批 row 就做 page 聚合 |
| [q-specstar-reindex-on-blob-edit.md](q-specstar-reindex-on-blob-edit.md) | blob 編輯後如何重新索引 |
