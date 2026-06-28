# 文件索引（Documentation）

> 這套文件也可用 mkdocs 建成可搜尋的網站（Material 主題、zh-TW、mermaid 圖）：
> `uv sync --group docs && uv run --group docs mkdocs serve`。設定見倉庫根目錄的
> `mkdocs.yml`；站內首頁是 [index.md](index.md)（開發者導覽）。

## 從這裡開始

| 文件 | 內容 | 對象 |
|---|---|---|
| [index.md](index.md) | **開發者導覽**：30 秒心智模型、分層架構圖、四個抽換點、倉庫地圖、「我想做 X 動哪裡」 | 第一次接觸這個 codebase 的人 |
| [architecture.md](architecture.md) | **系統架構（概觀）**：分層/Protocol、agent 回合資料流、LiteLLM↔OpenAI 事件正規化、sandbox/FileStore/sync 生命週期、KB 子系統、設計決策 | 想抓整體心智模型的人 |
| [subsystems/index.md](subsystems/index.md) | **子系統深入**：13 篇以真實程式碼為錨的逐子系統文件（職責、模組、Protocol、不變式、原始碼錨點） | 想鑽進某一塊的人 |

## 參考

| 文件 | 內容 |
|---|---|
| [glossary.md](glossary.md) | 詞彙表：領域名詞 + 各自歸哪個子系統（用語權威見 `CONTEXT.md`） |
| [decisions.md](decisions.md) | 設計決策登記簿：決策 / 理由 / 否決的替代方案 / 出處（含 issue 編號） |
| [contract.md](contract.md) | 線上契約（authoritative）：specstar 資料模型、完整 HTTP 路由、SSE 事件型別 |

## 開發 / 部署 / 使用

| 文件 | 內容 | 對象 |
|---|---|---|
| [development.md](development.md) | 開發者指南：環境/指令、慣例、TDD、如何新增 SSE 事件 / agent 工具 / KB chunker・embedder | 要在此 codebase 開發的人 |
| [adding-an-app.md](adding-an-app.md) · [workflows-authoring.md](workflows-authoring.md) · [skills-authoring.md](skills-authoring.md) | 新增一個 App / 撰寫 workflow / 共創 skills | 要擴充平台的人 |
| [deployment.md](deployment.md) | 部署與客製化：經 `create_app` 抽換各層、模型字串與環境變數、生產注意事項、#312 job pod 拆分 | 要部署或客製化的人 |
| [workflows.md](workflows.md) · [topic-hub.md](topic-hub.md) · [sandbox-host.md](sandbox-host.md) · [sci-plot.md](sci-plot.md) | 各功能子系統手冊 | 用到特定功能的人 |
| [user-guide.md](user-guide.md) | 使用者手冊：RCA 工作流程、VSCode 風格 UI、快捷鍵、KB 助理 | 使用這個應用的人 |
| [design-history.md](design-history.md) | 設計計畫與歷史：各功能動工前的 `plan-*` / `handoff-*` 與被否決的替代方案 | 想看設計演進的人 |

新手建議順序：[index.md](index.md) 抓方向 → [architecture.md](architecture.md) 抓全貌 →
[subsystems/](subsystems/index.md) 下鑽 → 開發看 [development.md](development.md) →
部署看 [deployment.md](deployment.md) → API 細節查 [contract.md](contract.md)。
