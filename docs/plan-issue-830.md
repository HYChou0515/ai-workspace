# Plan — #830 沙盒大小的硬上限由 record 帶給前端

issue #830（PR #825 review round 2 留下的「upper bounds not mirrored」）。issue 本身定了修法的四步；
本文件記 grill 過的決定（2026-09-21）、程式碼事實、phase 與測試。

## 背景

沙盒 modal 的 CPU / 記憶體欄位在客端只擋「> 0 + 文法對」；伺服器另有硬上限
`_MAX_CORES = 1024.0` / `_MAX_BYTES = 1 PiB`（`src/workspace_app/api/item_routes.py:147-148`），
超過只在 PUT 後 422。同一個欄位對 `0` 打字當下就紅、對 `1025` 卻要送出才知道——兩種回饋節奏。

issue 明令**不要**在前端寫一份 `MAX_CORES = 1024`：伺服器改了前端不會跟，兩邊各自的單元測試永遠綠。

## 程式碼事實（決定形狀的）

- `_MAX_CORES` / `_MAX_BYTES` 只在 `_validated_resources()` 用到；`_within` 是 `0 < value <= ceiling`
  （上限本身可以）。`_validated_resources()` 是純函式：吃 `_ResourcesBody`，回 `(cpu, memory)` 或 raise
  `HTTPException(422)`；路由另外兩種拒絕（404 未知 app、409 執行中）跟「值收不收」無關。
- GET `/a/{slug}/items/{id}/environment`（`_EnvironmentOut`）已經在講 `stated_* / effective_* /
  enforced_* / *_bound_by`；硬上限是同一個故事的最後一句。`_EnvironmentOut` 只在那一個路由被建構。
- 前端 `web/src/components/ItemEnvironmentSize.ts`：`isValidCpu(text)` / `isValidMemory(text)` 回 boolean；
  `normaliseMemory(text)` 回伺服器拼法、`parseSize(wire)` 回位元組；`toSizeString(1024**5)` 印 `1024T`
  （伺服器 `parse_size` 收的拼法；`formatBytes` 印的是 `1024.0 TB`，伺服器不收）。
- panel 的 `invalid: {cpu: boolean, memory: boolean}` 只能畫一句文法 hint；輸入框只在
  `enforced_* !== null` 時才畫（#825 規則）。
- CI：backend job 只裝 `uv`、沒有 `node_modules`；frontend job 只裝 `pnpm`、沒有 `uv`（`.github/workflows/ci.yml`）。
  本機 node v20 跑不了 TS。要伸出 `web/` 外的前端測試放 `web/tests/`（`tsconfig` 只編 `src`、docker 只
  `COPY web/`；先例 `web/tests/shippedWuiExample.test.ts`）。
- repo 裡沒有 Python 測試與前端測試共用同一份資料檔的先例。

## 已拍板的決定（grill 2026-09-21）

| # | 決定 | 內容 | 為什麼 |
|---|---|---|---|
| 1 | 伺服器 | `_EnvironmentOut` 加 `max_cpu_cores: float` / `max_memory_bytes: int`，請求當下讀 `_MAX_CORES` / `_MAX_BYTES` | issue 定的；同一個常數同時餵 GET 和 422 閘門 |
| 2 | 前端規則的形狀 | `cpuFault(text, max)` / `memoryFault(text, max)` 回 `null \| { type: "unreadable" \| "over", detail: string }`；舊 boolean 函式刪掉 | 同一個欄位要能顯示兩句 hint，boolean 分不出是哪種錯；一次 parse、一個判準；user：「一般是 type 和 detail」 |
| 3 | `detail` 裝資料 | `over` → 上限的伺服器拼法（cpu `"1024"`、記憶體 `"1024T"` = `toSizeString(max)`）；`unreadable` → 文法範例（cpu `"1 或 0.5"`、記憶體 `"512M、512MB 或 1.5G"`） | `ItemEnvironmentSize.ts` 是純函式沒有 locale；句子屬於 panel 的 `useT` |
| 4 | hint | i18n 每欄兩個 key：`itemenv.field.{cpu,memory}.unreadable` / `.over`，各帶 `{detail}`；超上限顯示「最多 1024 核。」/「最多 1024T。」 | issue 規則 3：教伺服器收的拼法，不印 `1024.0 TB` |
| 5 | Save | 任一欄位有 fault 就灰 | issue 定的 |
| 6 | parity | 答案卷 `tests/fixtures/item_size_parity.json`：每列「打的字 → 前端會送的拼法 → 收/拒」+ 兩個上限。Python 測試逐列**直接呼叫 `_validated_resources()`** 改分（毫秒級；卷子的 max 對 `_MAX_*` 常數）；`web/tests/itemSizeParity.test.ts` 逐列驗 `cpuFault/memoryFault(typed, 卷上的 max)` 與 `normaliseMemory(typed) === sent` | 兩個 CI job 工具鏈不同，跑不到對方；卷子由伺服器改分、常數一動就紅；不開 app 所以不慢 |
| 7 | 突變證明 | 伺服器：monkeypatch 常數 → GET 報的數字與 PUT 閘門一起動（路由級）；前端：假 record 上限 7 → 打 8 → hint 寫 7 | issue 4(b) 在 CI 上是組合證明：常數→GET、record→hint、常數→卷→前端判準 |
| 8 | 缺欄位 | 不處理：wire type 必有 `number`，沒有 fallback | 前後端一起更新；user：「這題不重要，選最簡單的」 |

## 不做的

- 伺服器的上限值、`_within`、`parse_size`、422 訊息不動。
- 不加 `<input type=number max>` 屬性（issue 沒要）。
- 不重構 `normaliseMemory` / `parseSize`：比較位元組就用現有的 `parseSize(normaliseMemory(text))`。
- 不記 `docs/migrations.md`（純新增回應欄位、無旋鈕、運營方不用動手）。
- 不做跨語言 e2e（Playwright 不在 CI）。
- 上一輪 review 抓到、#830 之前就有的兩條，不在這條 PR（要不要開票由 user 決定）：
  真 Chromium 的 `<input type=number>` 對 `1e309` / `1e` 把 value 交成 `""`（`badInput`），前端當「用預設」
  → Save 送 `cpu_cores: null` 靜默清掉設定值；阿拉伯-印度數字 `٥١٢M` 伺服器 `str.isdigit()` 收、前端拒。

## Phases（flat integer，一個 commit 一個）

- **P1 後端**：`_EnvironmentOut` 兩欄 + 路由填值。測試：`test_the_environment_reports_the_ceilings_the_put_refuses_against`
  （monkeypatch `_MAX_CORES=7`、`_MAX_BYTES=3G` → GET 報 7 / 3G；PUT 7 → 200、7.5 → 422、3G → 200、3073M → 422）。
- **P2 前端規則**：`cpuFault` / `memoryFault` `{type, detail}`；刪 `isValidCpu` / `isValidMemory`；
  `ItemEnvironmentSize.test.ts` 改寫（每個舊案例保留、加上限案例、加「上限是 record 給的不是模組自己的」案例）。
- **P3 前端 panel / modal / i18n**：`invalid` prop → `fault: {cpu, memory}`；hint 照 `type` 挑 key、`detail` 插值；
  `canSave` 看 fault；mapper 加兩欄。測試：panel（over 案 hint 帶 record 的數字、unreadable 案帶文法）、
  modal（record 上限 7：打 8 → 紅 + hint 7 + Save 灰；打 7 → 恢復；記憶體同）、api mapper。
- **P4 答案卷**：`tests/fixtures/item_size_parity.json` + `tests/quota/test_item_size_parity.py`
  （`_validated_resources()` 改分、對 `_MAX_*`、正控制：每維度兩種判決都有、至少一列「超上限」）
  + `web/tests/itemSizeParity.test.ts`（逐列 fault、`Number(typed) === sent`、`normaliseMemory(typed) === sent`、
  超上限列必須是 `over` 不是 `unreadable`）。卷上的 `sent` 由真前端程式碼導出，不心算。
- **P5 review → CI**：push 後砍 CI；四鏡頭（conformance / veracity / defect / regression）各自 worktree 平行；
  乾淨後 CI 對最終 sha。

## 測試（先紅）

每條新測試先在未修的程式碼上跑紅，再寫綠；每個「X 被 Y 釘住」的宣稱用檔案拷貝突變 X、看只有 Y 紅、還原。

| 測試 | 未修時紅在哪 | 突變探針 |
|---|---|---|
| P1 路由級 | `KeyError: 'max_cpu_cores'` | 路由寫死 `1024.0` → `assert 1024.0 == 7.0` |
| P2 size | `cpuFault is not a function` | `n <= max` 改 `n <= 1024` → 「上限是 record 給的」案例紅 |
| P3 panel / modal | hint 沒有數字 / `isValidCpu` 不存在 | hint 寫死 `"1024"` → over 案例紅 |
| P4 Python 卷 | — （改卷子的測試；證明會紅：翻一列判決 → 點名那列；`_MAX_CORES` 改 2048 → 紅在上限那行） | |
| P4 前端卷 | `cpuFault` 不存在 | `over` 改回傳 `unreadable` → 正控制案例紅 |

## As-built（2026-09-21，P1–P4 對 `origin/master` `ca9d33a8`）

每條測試都先在未修的程式碼上跑紅，突變探針用檔案拷貝、跑完還原：

| Phase | 未修時 | 突變 → 紅在哪 |
|---|---|---|
| P1 `ef902719` | `KeyError: 'max_cpu_cores'` | route 寫死 `max_cpu_cores=1024.0` → 只有這條紅，`assert 1024.0 == 7.0` |
| P2 `0f076105` | `cpuFault is not a function` ×2、`memoryFault` ×2 | 兩個比較改成模組常數、detail 寫死 → 只有兩條「上限是 record 給的」紅 |
| P3 `0c02565b` | mapper `expected undefined to be 7`；panel 兩條；modal 整檔（還 import 舊名） | panel 的 cpu hint `detail` 寫死 `"1024"` → 4 條紅（panel 兩條 hint、modal record-7、modal over/unreadable） |
| P4 `cab0ecc6` | —（改卷子的測試） | 翻 `1025` 為 accepted → `cpu '1025': server says False`；`_MAX_CORES` 改 2048 → `stale sheet … assert 1024 == 2048.0`；前端 `over` 改回 `unreadable` → `itemSizeParity.test.ts` 裡只有正控制那條紅（整組跑另有 Size 檔兩條） |

兩個 plan 沒寫到的細節：`detail` 是資料、不能含「或」這種 locale 字，範例用 ` / ` 接
（`"1 / 0.5"`、`"512M / 512MB / 1.5G"`），「例如 {detail}」留在 i18n；`toSizeString` 多了兩個 overload 簽名
（`number` 進就 `string` 出），純型別、沒有 runtime 變化，是 `detail: toSizeString(max)` 要過 `tsc` 的前提。
Python 卷子改分 2.9 秒（import），上一版開 app 的做法 8–11 秒。

## Live check（2026-09-21，worktree build on 127.0.0.1:8258，`per_app.default` 2 核 / 512M + `per_user` 4 核 / 8G，真 Chromium 1280）

Playground item，未啟動。`GET …/environment` 回 `max_cpu_cores: 1024.0, max_memory_bytes: 1125899906842624`；
`PUT …/resources {"cpu_cores": 2048}` 仍 422、訊息同以前。modal 裡逐步（每列是 Playwright 讀回的 DOM）：

| 打的字 | `aria-invalid` | hint | Save |
|---|---|---|---|
| CPU `2048` | true | At most 1024 cores. | 灰 |
| CPU `1024` | — | — | 可按 |
| CPU `0` | true | More than 0 — e.g. 1 / 0.5. | 灰 |
| 記憶體 `1025T` | true | At most 1024T. | 灰 |
| 記憶體 `2P` | true | A number with a unit — e.g. 512M / 512MB / 1.5G. | 灰 |
| 記憶體 `1024T` | — | — | 可按 |

cpu input 沒有 `max` 屬性（「不做的」）。存 `2` / `1024T` → record `stated_memory_bytes = 1125899906842624`、
`memory_bound_by = "app"`。截圖 `live2-cpu-2048.png` / `live2-mem-1025T.png` / `live2-after-save.png`（job tmp，不進 repo）。

## Review round 1（2026-09-21，conformance / veracity / defect / regression 四把平行，各自一棵 worktree，對 `02691b73`）

最壞發現：**MEDIUM**，一條，測試守衛。

- **Veracity**：MEDIUM — panel 裡「`over` 選 `.over` key、否則 `.unreadable`」那一行沒有測試釘住：改成永遠
  `.unreadable`（或永遠 `.over`）→ 7 檔 96 條全綠、typecheck 綠，因為每條 hint 測試都只斷言數字，沒斷言句子。
  修法：panel / modal 四個案例補斷言句子（`最多|At most` vs `要大於|More than 0` / `數字加單位|A number with a unit`）。
  重跑：永遠 `.unreadable` → 3 條紅、永遠 `.over` → 2 條紅。LOW ×3 措辭：PR body 的「只有兩條紅」整組跑是 3
  （Size 兩條 + modal record-7）；「left as separate reports」其實沒開票，只記在本文件；`max` 屬性出處寫錯成決定 8、
  modal 一句註解「typed back 會被拒」對欄位不成立（`normaliseMemory` 收 `1024.0 TB`）。
- **Conformance**：none。它另外做了字面的 4(b)：伺服器 `_MAX_CORES=2048` 的真 GET body 餵真 modal →
  「最多 2048 核。」。備註：`toSizeString` overload 沒寫進 plan（型別、已補上一段）。
- **Defect**：none。55 個 cpu 文字 + 78 個記憶體文字，前端 vs 真路由（含 pydantic JSON 解析）vs `_validated_resources()`
  直呼三方零分歧；真 Chromium 逐字打進 `<input type=number>` 能產生的每個字串都在探針裡。備註（非缺陷）：22 位以上
  無單位的天文數字 `normaliseMemory` 回非 wire 字串，hint 說「文法」而不是「上限」，Save 照灰、伺服器照 422。
- **Regression**：LOW — 新 bundle 打到舊 pod（ingress 依 item id 雜湊、`staleTime` 30 秒）時 hint 印「最多 undefined 核。」、
  Save 灰；這是決定 8 拍板的「不處理」，兩把鏡頭都點出「一起更新」是以 image 為單位不是以 request 為單位。
  其餘：109 / 25 / 30 個輸入的 `normaliseMemory` / `parseSize` / `toSizeString` 新舊零差異；新拒絕的全是超上限且伺服器
  也 422；#825 的 35 條 modal 行為全在。

修法形狀：補測試釘子 + 文字，沒有換機制 → 不再開一輪，CI 對最終 sha。

## 驗證（DoD）

- targeted 測試 + `ruff check` / `ruff format --check` / `ty check` / `pnpm run typecheck`。
- 真 Chromium 親自按：worktree build、config 要 `resources.per_app.default: {cpu: 2, memory: 512M}`
  （只設 `per_user` 時 `enforced_*` 是 null，panel 畫「無法確認」沒有輸入框）+ `per_user`；打 `2048` /
  `1024` / `0` / `1025T` / `2P` / `1024T` 六個狀態，讀回 `aria-invalid`、hint、Save。
- PR body 末尾「在 prod 環境怎麼驗證」：curl GET 看兩個 key；modal 打 `2048` 看 hint；PUT 2048 仍 422；舊行為都在。
