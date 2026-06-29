# Plan — #331 topic-hub 沒有 step 以外的詳細進度

> 來源：#331「topic-hub 沒有 step 以外的詳細進度」+ 內文「#283 沒修復？」。
> 經 /grill-me 收斂：這是 #283 進度視覺化的**回歸**，不是新功能。

## 根因（讀碼確認）

#283 把整套進度視覺化（phase diagram + metrics + step board + timeline/Gantt +
視圖切換 + no-op/result/failures + 連線中斷警告 + run 層級 Stop）做進
`WorkflowRunPanel`，而它只被 `WorkflowRunSection` 掛載。後來 #200 把所有 App 改成
`ItemChatShell` 多聊天介面、**明文 retire 掉 `WorkflowRunSection`**，於是整個
`WorkflowRunPanel` 變成**孤兒死碼**——topic-hub 永遠 render 不到它。

topic-hub 現在唯一的進度 UI 是 `AgentPanel` 裡的陽春 `<ProgressBar>`（色塊 + 一行
「step N · 標題」）。agent 的 reasoning/工具卡其實**有**串進 chat feed
（`drive_turn` → `enqueue(chat_key=run.chat_id)` → `_run_turn` 對每個 event
`publish` → 廣播給該 chat 的所有訂閱者，FE `useItemChat` 收得到），但「step 結構層級」
的細節通通不見了。

**這是純前端問題**：`useRun` 回的 `WorkflowRunDTO` 已帶齊
`workflow_id / status / phases / steps(started,ended,attempts) / failures /
started / ended / result / pending_decision / pending_steer`——富面板要的資料全在，
零後端變動。

## 鎖定的設計決策（/grill-me）

- **修法**＝補完 #283：把豐富進度視圖接進 workflow chat（`ItemChatPanel`），取代陽春
  `ProgressBar`。
- **收合條 → 展開細節**：收合（**預設**）＝那條 bar（狀態 + 色塊 + 「step N · 標題」+
  Stop + 展開鈕）；展開＝完整進度面板。`usePersistentBoolean` 記住、跨 run/reload 保留。
- **驗收清單（全部要滿足，不砍範圍）**：
  - 細節維度：phase diagram（含批次子進度 12/20·1 failed）、step board（每步狀態/耗時/
    重試）、step 以內 live 細節（agent reasoning+工具卡 / deterministic stdout / step
    生命週期行，都在 feed）、timeline/Gantt + `[步驟清單|時間軸]` 切換、metrics。
  - 終局說人話：done（result 訊息 / JSON）、done no-op 橫幅、error（failures 清單 +
    result.error）、cancelled、pending、連線中斷警告。
  - 控制項：run 層級 **Stop**（`useCancelRun`，現在 topic-hub 完全沒有）、gate 決策（已有）、
    steer（已有）、展開/收合 + 記住。
- **不做**（grill 拍板）：
  - step↔活動 drill-down（feed 維持時間順序交錯就好）。
  - 另做 run 專屬清單（chat switcher 把每個 run 當分頁，#132 已涵蓋）。
- **排版（I1=甲）**：`ItemChatPanel` 由上而下＝進度面板 → gate/steer 卡（pin）→ feed。
- **範圍**：修在 `ItemChatPanel` 這層 → 所有有 workflow 的 App 一起受惠；RCA 無 run、
  `phases` 為空 → 不顯示，零影響。

## 影響面

- **前端**：新增 `WorkflowProgress`（收合/展開）、接進 `ItemChatShell`、`AgentPanel` 拿掉
  `phases`/`ProgressBar` + 修誤導註解、刪 `WorkflowRunSection` + `WorkflowRunPanel`
  孤兒、i18n。
- **後端**：無。
- **資料**：無 schema 變動（timeline 吃既有 `started/ended ms`）。

## Phases（flat integer，照 CLAUDE.md；走 /tdd，FE=vitest）

- **Phase 1** — `WorkflowProgress.tsx`：把孤兒 `WorkflowRunPanel` 的內容整碗端來、扣掉
  gate 決策卡（`WorkflowDecisionCard`）；`WorkflowMetrics` 一併搬入；含狀態、Stop
  (`useCancelRun`)、連線中斷警告、`WorkflowPhaseDiagram`、metrics、`[步驟清單|時間軸]`
  切換、`WorkflowStepBoard`/`WorkflowTimeline`、no-op 橫幅、result 訊息/JSON、failures。
  外包收合層（收合=bar、展開=全部、`usePersistentBoolean` 預設收合、Stop 在收合條也看得到）。
  /tdd（vitest）。
- **Phase 2** — 接線：`ItemChatPanel` 在 `<AgentPanel>` 上方渲染 `WorkflowProgress`
  （它本來就有 `useRun`/`useCancelRun`），順序＝進度面板 → gate/steer → feed（I1 甲）。
  `AgentPanel` 拿掉 `phases` prop + 內部 `ProgressBar` + 那句指向不存在 run header 的
  誤導 composer 註解。/tdd（vitest）。
- **Phase 3** — 刪孤兒死碼：`WorkflowRunSection.tsx`(+test)、`WorkflowRunPanel.tsx`(+test)；
  更新 `WorkflowLaunchDialog` 過時註解；清掉殘留 import。
- **Phase 4** — i18n（zh-TW + en）：所有新字串走 `useT`。
- **Phase 5** — 收尾：full vitest + `pnpm typecheck` + `pnpm build`；後端 FE-only 不動，
  仍跑 `ruff` + `ty`(unscoped) + coverage gate 確認沒踩到；commit → PR → 等 CI 綠 + 無衝突
  → merge。
