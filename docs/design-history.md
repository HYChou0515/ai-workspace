# 設計計畫與歷史

這裡收錄的是**設計演進的歷史紀錄**：每個功能在動工前的 grill-me / plan 文件、被否決的替代方案、以及給接手者的 handoff。它們**不是**目前架構的權威說明——權威說明請看 [系統架構](architecture.md)、[線上契約](contract.md) 與 [開發者指南](development.md)。

> 為什麼留著？這些文件記錄了「**為什麼這樣設計、當初考慮過哪些路、為什麼不走**」。當你想改某個決策時，先回來看看它原本被否決的理由，通常能省下重踩一次坑的時間。

---

## 平台與 App 模板

| 文件 | 主題 |
|---|---|
| [plan-app-templates.md](plan-app-templates.md) | #89 RCA → 多 App 平台：`apps/<slug>/` 模板、WorkItemBase、三層 agent resolve |
| [plan-backend.md](plan-backend.md) | 最初的後端設計計畫（分層 / Protocol / SSE 的原始脈絡） |
| [plan-frontend.md](plan-frontend.md) | 最初的前端設計計畫（VSCode 風格 UI） |
| [plan-topic-hub.md](plan-topic-hub.md) | Topic Hub App：跨 collection 探究工作區、檔案記憶、多 chat |
| [plan-collab-workspace.md](plan-collab-workspace.md) | #43 多人協作 workspace（檔案 + chat，無 notebook） |
| [plan-permissions.md](plan-permissions.md) · [plan-permissions-pr2-handoff.md](plan-permissions-pr2-handoff.md) | 權限模型設計與分階段交接 |

## 知識庫（KB）與檢索

| 文件 | 主題 |
|---|---|
| [plan-kb-parsers.md](plan-kb-parsers.md) | #39 KB 解析器：parser 吐整檔 Document、splitter 掌管切塊粒度 |
| [plan-llamaindex-ingest.md](plan-llamaindex-ingest.md) | 以 LlamaIndex 重構攝取管線 |
| [plan-kb-retrieval-enhancements.md](plan-kb-retrieval-enhancements.md) | multi-query / HyDE / rerank 的 enhancement 旋鈕設計 |
| [plan-retrieval-llm-refactor.md](plan-retrieval-llm-refactor.md) | 檢索 LLM 介面重構 |
| [plan-llm-wiki.md](plan-llm-wiki.md) | #50 LLM wiki：與 chunk-RAG 平行的第二條維基管線 |
| [plan-context-cards.md](plan-context-cards.md) | #106 Context cards：輕量、確定性的詞彙卡（glossary） |
| [plan-collab-kb.md](plan-collab-kb.md) | KB 協作設計 |

## Workflows

| 文件 | 主題 |
|---|---|
| [plan-workflows.md](plan-workflows.md) | #100 API 觸發的 headless workflow：FS-as-journal、produce→review→commit |
| [workflows-frontend-brief.md](workflows-frontend-brief.md) | Workflows 前端設計 brief |
| [plan-make-deck-runtime-craft.md](plan-make-deck-runtime-craft.md) | #284 make_deck：意圖 → 多模態子代理迴圈產投影片 |

## Sandbox 與基礎設施

| 文件 | 主題 |
|---|---|
| [plan-http-sandbox.md](plan-http-sandbox.md) | #60 HTTP sandbox host：把 sandbox 拆成獨立 HTTP 服務 |
| [plan-sandbox-sot.md](plan-sandbox-sot.md) | sandbox 真相來源（source-of-truth）設計 |
| [plan-llm-failover.md](plan-llm-failover.md) | LLM failover / 多供應商備援 |
| [plan-sanity-checks.md](plan-sanity-checks.md) | 開機健康檢查 / sanity matrix |
| [plan-repetition-guard.md](plan-repetition-guard.md) | #113 重複迴圈偵測與優雅阻擋 |
| [plan-skills-and-tools.md](plan-skills-and-tools.md) | Skills 與 tools 套件設計 |
| [plan-sci-plot.md](plan-sci-plot.md) | #285 sci-plot 科學繪圖工具 |
| [plan-read-image.md](plan-read-image.md) | #112 read_image：VLM-over-workspace-image 工具 |
| [plan-code-qa.md](plan-code-qa.md) | 程式碼 QA 設計 |

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
[479](plan-issue-479.md)

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
