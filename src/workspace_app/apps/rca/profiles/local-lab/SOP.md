# 跟 RCA agent 一起工作的操作指南

> 這份是寫給你(使用者)看的;agent 的版本在 system prompt 裡。
> 整段流程 agent 全程用 **繁體中文** 跟你對答。

## 開始前手邊先有

1. **一份 `wafer_id × defect_count` 的資料**(對話裡列出或上傳 CSV 都可以)
2. **scan stage** — defect 是在哪個 measurement / inspection stage 被抓到的
3. **defect type** — 是哪一種 defect
4. **(可選)threshold** — `defect_count` 多少以上算 defective
   - 不想決定 → agent 會依分布提一個 **soft** 切點問你 OK 嗎
   - 想給但不確定 hard / soft → 直接給數字、預設當 soft
   - 想硬切 → 明說「要 hard」

## Agent 會跑的 6 步

| # | 階段 | Agent 做的事 | 你要做的事 |
|---|---|---|---|
| 1 | 收集問題 | 問你上面 4 項資料,寫到 `./step1-brief/brief.md` | 回答它的問題;確認 brief.md 寫對 |
| 2 | 下載 + 切群 + (可選) Q-Time | `wafer-history` 撈製程歷史 → `infer_modules` sub-agent 推斷 step→module 寫 `module-map.csv` → 依 threshold 切 defective / baseline → (若 defect 跟 queue/wait 有關)算 cross-module boundary pair + KB 補幾條 → `qtime-data` 出 `qtimes.csv` | **對齊 scan stage 的 `step_number` 切點** + (若分得不準)補一兩個關鍵 step 該歸哪個 module |
| 3 | 跑分析 | 跑 `rank-factors-*` + 寫 ad-hoc Python script,所有 CSV / PNG / script 進 `./step3-analysis/`(script 進 `scripts/` 子資料夾) | 看 agent 出的 ranking 跟圖;想要更細切片直接說 |
| 4 | KB 過濾 | 對 top-K factors 用 `ask_knowledge_base` 查歷史案件 / 製程文件,去掉撐不起 hypothesis 的 | 必要時補充 KB 裡沒有的 in-house 知識給 agent |
| 5 | 跟你 review | 整理 step 4 留下來的 factor + hypothesis 給你看 | **判斷有沒有 insight** — 沒有 → 叫 agent 回 step 4 換條件再跑;有 → 進 step 6 |
| 6 | 寫 report | 跟你協作完成 `./step6-report/report.v1.md`(後續版本 `./step6-report/report.v{N+1}.md`) | 改到你滿意 → **自己在 FE 上按「Close as resolved」收尾** |

## Workspace 檔案怎麼擺

Agent 跑完一輪後 workspace 大致長這樣(按 SOP 6 步分子資料夾,不會把幾十個檔案散落根目錄):

```
/step1-brief/brief.md
/step2-data/{defects,wafer-history,measurements}.csv
/step2-data/{module-map,wafer-history-with-module,qtime-pairs,qtimes}.csv  ← infer_modules + qtime-data
/step2-data/scripts/{join_modules,select_qtime_pairs}.py
/step3-analysis/{rank-factors-*.csv, *.png, scripts/*.py}
/step6-report/report.v{N}.md
```

跨 variant 比較(如 threshold=6 vs =9)agent 會在 CSV / PNG 加 suffix 並存,例如 `rank-factors-categorical-th6.csv`,你可以 A/B 對照。

## 一些事先講清楚

- **圖檔位置**:agent 跑分析時畫的圖預設輸出到 `./step3-analysis/`(屬於該 step 的話),FE 上直接看得到。
- **想 abort / 重來**:跟 agent 說「重來」、或直接關掉 investigation 開新的。
- **close 權限**:agent **沒有** 自動 close investigation 的權限 — 收尾一定要你按按鈕。這是 by design(不讓 agent 自己 say "done" 就把 case 關掉)。
- **沒有 KB 內容也能跑**:step 4 KB 過濾若 collection 是空的,agent 會跳過或直接問你「這 step 跟此 defect type 有沒有歷史關聯?」— 你回答,流程繼續。
- **報告版本切換**:`./step6-report/` 下的 `report.v1.md` / `report.v2.md` / … FE 的 Final report 檢視器會自動偵測、顯示版本切換 + Superseded 標記,跟放根目錄一樣。
