# 文件索引（Documentation）

| 文件 | 內容 | 對象 |
|---|---|---|
| [architecture.md](architecture.md) | 系統架構：分層/Protocol、agent 回合資料流、LiteLLM↔OpenAI 事件正規化、sandbox/FileStore/sync 生命週期、user-ns 隔離、**知識庫（KB）子系統**、設計決策 | 想了解整體設計的人 |
| [development.md](development.md) | 開發者指南：環境/指令、慣例、TDD 流程、如何新增 SSE 事件/agent 工具/檔案 renderer/**KB chunker/embedder** | 要在此 codebase 開發的人 |
| [deployment.md](deployment.md) | 部署與客製化：如何透過 `create_app` 抽換 sandbox / FileStore / AgentRunner / AgentConfig / 範本 profile / **KB embedder/檢索 LLM**，LLM 模型字串與環境變數，生產注意事項 | 要部署或客製化的人 |
| [user-guide.md](user-guide.md) | 使用者手冊：RCA 工作流程、VSCode 風格 UI 各功能、快捷鍵、**知識庫（KB）助理** | 使用這個應用的人 |
| [contract.md](contract.md) | 線上契約（authoritative）：specstar 資料模型（含 KB）、完整 HTTP 路由（含 `/kb`）、SSE 事件型別、agent 檔案慣例 | 串接 API / 前後端對齊 |
| [plan-backend.md](plan-backend.md) · [plan-frontend.md](plan-frontend.md) | 原始設計計畫（歷史脈絡；KB 為其後新增，見 architecture/contract） | 想看設計演進的人 |

新手建議順序：先看 [architecture.md](architecture.md) 抓全貌 → 開發看
[development.md](development.md) → 部署/客製化看 [deployment.md](deployment.md) →
API 細節查 [contract.md](contract.md)。
