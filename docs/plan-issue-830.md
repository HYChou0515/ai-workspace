# Plan — #830 the size fields learn the server's hard ceilings from the record

Issue #830 (left by PR #825 review round 2, "upper bounds not mirrored").
The issue itself states the fix; this records the code facts, the one decision
it left open, and the evidence.

## 背景

沙盒 modal 的 CPU / 記憶體欄位在客端只擋「> 0 + 文法對」；伺服器另有硬上限
`_MAX_CORES = 1024.0` / `_MAX_BYTES = 1 PiB`（`api/item_routes.py`），超過只在
PUT 後 422。同一個欄位對 `0` 打字當下就紅、對 `1025` 卻要送出才知道。

issue 明令**不要**在前端寫一份 `MAX_CORES = 1024`：伺服器改了前端不會跟，兩邊各自的
單元測試永遠綠。

## 程式碼事實（決定形狀的）

- `_MAX_CORES` / `_MAX_BYTES` 只在 `_validated_resources` 用到；`_within` 是
  `0 < value <= ceiling`（上限本身可以）。GET `/environment`（`_EnvironmentOut`）
  已經在講 `stated_* / effective_* / enforced_* / *_bound_by`，硬上限是同一個故事的最後一句。
- 前端 `ItemEnvironmentSize.ts` 的 `isValidCpu` / `isValidMemory` 回 boolean；panel 的 hint
  只有一句文法。`toSizeString(1024**5)` 印 `1024T`——伺服器 `parse_size` 收的拼法。
- backend CI job 沒有 `node_modules`、frontend job 沒有 `uv`：一條「伺服器當 oracle」的
  parity 測試不能在同一個 process 裡同時跑到兩邊。
- `web/tests/` 是「要伸出 `web/` 外」的測試的家（`tsconfig` 只編 `src`；
  `shippedWuiExample.test.ts` 已這樣做）。

## 決定

| # | 決定 | 選擇 | 為什麼 |
|---|---|---|---|
| 1 | 上限怎麼到前端 | `_EnvironmentOut.max_cpu_cores` / `max_memory_bytes`，值在請求當下讀 `_MAX_CORES` / `_MAX_BYTES` | issue 指定；同一個常數同時餵 GET 和 422 |
| 2 | 前端規則的形狀 | `cpuFault(text, max)` / `memoryFault(text, max)` → `null \| "unreadable" \| "over"`；舊 boolean 函式刪 | hint 要分「文法錯」和「超上限」；兩套規則不並存 |
| 3 | 記憶體只解析一次 | `memoryBytes(text)` 是那次解析；`normaliseMemory` = 它的 `toSizeString`；`parseSize`（把自己的輸出再 parse）刪 | `memoryFault` 不該把自己剛印出的字再讀回來 |
| 4 | 上限缺席（rollout 中打到舊 API pod） | mapper `?? Infinity` → 客端不擋、伺服器照 422 | 不發明數字；= #830 之前的行為 |
| 5 | hint 文案 | `itemenv.field.cpu.max`「最多 {max} 核。」/ `itemenv.field.memory.max`「最多 {max}。」；max 用 `String(max)` / `toSizeString(max)`（`1024` / `1024T`） | 教伺服器收的拼法，不印 `1.0 PiB` |
| 6 | `<input type=number max>` | 有限時帶 `max` | 瀏覽器內建的停點，同一個數字 |
| 7 | parity 測試 | 答案卷 `tests/fixtures/item_size_parity.json`：Python 端逐列 PUT 真路由、GET 對上限（伺服器改分）；`web/tests/itemSizeParity.test.ts` 逐列跑 `cpuFault/memoryFault(typed, 卷上的 max)` 且 `normaliseMemory(typed) === sent` | 兩個 CI job 工具鏈不同；卷子由伺服器改分，production 前端不含這個數字 |
| 8 | `docs/migrations.md` | 不記 | 純新增回應欄位、無旋鈕、運營方不用動手 |

## 不做的

- 伺服器的上限值、`_within`、`parse_size` 不動。
- `formatBytes` 顯示格式不動（hint 故意不用它）。
- 不做跨語言的 e2e（Playwright 不在 CI）。

## Phases

- **P1** 後端：`_EnvironmentOut` 兩欄；測試 monkeypatch 常數 → GET 與 PUT 閘門一起動。
- **P2** 前端：fault 函式、mapper、panel hint + `max`、modal、i18n；modal / panel / size / api 測試。
- **P3** 答案卷 + 兩邊的 parity 測試。
- **P4** 本文件 + `plan-sandbox-modal-redo.md` 指回。
- **P5** review（conformance / veracity / defect / regression）→ CI。

## 驗證（做完當下的證據）

- P1 突變：route 寫死 `max_cpu_cores=1024.0` → 只有
  `test_the_environment_reports_the_ceilings_the_put_refuses_against` 紅（`assert 1024.0 == 7.0`）。
- P2 突變：panel hint 寫死 `"1024"` → 2 條紅（panel over 案、modal record-7 案）；
  `cpuFault`/`memoryFault` 改比模組常數 → 4 條紅（size 兩條 record-ceiling、modal record-7、modal no-ceiling）。
- P3 探針：卷上 `1025` 翻成 accepted → Python 點名 `cpu '1025' -> 422 …`；
  `_MAX_CORES` 改 2048 → Python 紅在 `stale sheet: re-read the server`；
  卷子照 2048 重改（漏了 `1024.5` 那列，也被點名）→ Python 綠、前端**零改動**綠。
- `sent` 拼法由真前端程式碼導出（`1024.5T` → `1049088G`，不是心算）。

## Live check（2026-09-21，worktree build on 127.0.0.1:8258，`per_app.default` 2 核 / 512M + `per_user` 4 核 / 8G，真 Chromium 1280）

Playground item，未啟動。`GET …/environment` 回 `max_cpu_cores: 1024.0, max_memory_bytes: 1125899906842624`；
`PUT …/resources {"cpu_cores": 2048}` 仍 422、訊息同以前。modal 裡逐步（每列是 Playwright 讀回的 DOM）：

| 打的字 | `aria-invalid` | hint | Save |
|---|---|---|---|
| CPU `2048` | true | At most 1024 cores. | 灰 |
| CPU `1024` | — | — | 可按 |
| CPU `0` | true | More than 0 — e.g. 1 or 0.5. | 灰 |
| 記憶體 `1025T` | true | At most 1024T. | 灰 |
| 記憶體 `2P` | true | A number with a unit — e.g. 512M, 512MB or 1.5G. | 灰 |
| 記憶體 `1024T` | — | — | 可按 |

CPU 欄的 `max` 屬性 = `1024`。存 `2` / `1024T` → 一個 PUT、record `stated_memory_bytes = 1125899906842624`、
`memory_bound_by = "app"`，欄位重讀回 `1024T`，clamp 句「You set 1024.0 TB; 512.0 MB is in effect」是既有的
`formatBytes` 顯示格式。截圖 `live-cpu-2048.png` / `live-mem-1025T.png` / `live-after-save.png`（job tmp，不進 repo）。
