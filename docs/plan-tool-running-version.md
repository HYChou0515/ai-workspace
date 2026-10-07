# Plan — 第三方工具「沙盒裡實際在跑哪一版」

Grilled 2026-10-07 on master `486ce318`. 每條決定標來源：**[user]** = 使用者決定；**[mine]** = 我定的實作細節，列出來給推翻。

## 問題

使用者回報：第三方工具（#674）發了新版，工具選單顯示新版，但 AI 實際執行時跑的是舊版。

機制（master 上查證）：

- 每一輪對話開頭，`turn_context.resolve_item_tools` 叫 sandbox-host 解析 manifest，拿到**最新**的
  `{sha, version, commands}`（`tooling/external.py` 的 `ExternalTools`）。工具選單（`tools_routes`）與
  AI 讀到的工具說明（`tooling/registry.py:describe_command` 的 bundle 那一行）用的都是**這份**。
- 沙盒在**建立的那一刻**把當時那個 sha 的 bundle 掛到 `/.tools/<name>`（sandbox-host
  `local_process.py:_build_tools_view`，連到 `ext/<sha>`），之後不再換。
- `confine_to_mounted` 只檢查「有沒有掛」，**刻意不比 sha**：使用中途發版不能把工具抽走。
- 所以活著的沙盒建得比發版早時，選單和 AI 說的是新版，`exec` 跑的是舊版，而且沒有任何地方說出這件事。
- 另一個缺口：app 只在「這個 pod 自己建的沙盒」時知道掛了什麼（`InvestigationSession.tools`），別的 pod
  建的是 `None`（不知道）。

探針（2026-10-07，真 `InvestigationRegistry` + http 沙盒替身，含「沒關閉」對照組）證實：關閉沙盒後下一輪
**會**以新 sha 重建，跨 pod 也一樣——所以修的不是重建，是「說出真話」。

## 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 沙盒裡的版本與最新版不同時**只告知**：不自動回收、不拒絕執行。 | [user] |
| D2 | AI 從**工具說明的 bundle 那一行**得知：版本號改成沙盒實際掛的；不同時多一句「最新是 vN，關閉沙盒就會換」。相同時 prompt 一字不多。 | [user] |
| D3 | 工具選單該列顯示「執行中 v1 · 最新 v2」，清單上方一顆「**關閉沙盒以更新**」按鈕（多個工具不一致也只一顆），按下走現有的關閉流程。 | [user] |
| D4 | 查不到實際版本（功能上線前建的沙盒）**當作最新版**：照最新版顯示、不放按鈕。 | [user] |
| D5 | 「實際版本」= 建立沙盒那一刻解析到的 `{sha, version}`，跟位址一起寫進共用的 `_SandboxAddress` 那一列；兩條建立的路（turn、非 turn 的叫醒）都寫。sandbox-host 不改。 | [mine] |
| D6 | 只做第三方工具。內建工具跟 image 一起重部署，不會新舊並存；view plugin 的沙盒指令不是 agent 工具、不在選單裡。 | [mine] |
| D7 | 沒有活著的沙盒時，只顯示版本號、不放按鈕——下一次建的就是最新版。 | [mine] |
| D8 | 「一不一致」比 **sha**，不比版本字串：同一個版本號重發一次也算新的一版。 | [mine] |
| D9 | 按鈕只給**關得掉**的人（與 `DELETE /me/resources/live/{item}` 同一道閘：擁有者、superuser、`change_permission`）；其他人看到「沙盒關閉後就會更新」的說明，沒有按鈕。閘抽成一個函式，兩邊共用。 | [mine] |
| D10 | 這一輪的「已掛載」改成問 registry：本 pod 的 session，否則讀共用那一列，兩者都先探活（http）。副作用：`confine_to_mounted` 對**別的 pod 建的沙盒**也知道掛了什麼了——發版後才加的新工具，會以理由拒絕，而不是交給模型一個不存在的 launcher。 | [mine] |
| D11 | 沙盒建好之後才加進 app 的工具（沙盒裡沒有）：選單該列顯示「目前的沙盒裡沒有這個工具」，算進「關閉沙盒以更新」同一顆按鈕——關閉同樣能修好它。 | [mine，review round 1] |
| D12 | 「已掛載」的查詢**有上限且不會失敗**：限時 3 秒（`mounted_probe_timeout_s`）。主機出錯或逾時時，**本 pod 自己建的紀錄照用**（主機沒回答不代表沙盒變了；丟掉它會讓 turn 再交出沙盒沒有的工具——review round 2），只有確定「沙盒不在了」才放掉；只能從共用那一列得知的別人的紀錄則當「查不到」（D4）。選單與 turn 的組裝以前完全不碰沙盒，不能因為這個功能變成會 500 或卡住；turn 只在 app **自己宣告**、模型會拿到的工具存在時才問（部署的 view plugin 也掛在沙盒裡，但不是任何 agent 的工具）。 | [mine，review round 1–2] |
| D13 | 用詞是「不同」不是「較舊」：比的是 sha（D8），只知道不一樣，不知道哪邊新。給 AI 的那句是「沙盒跑的是 X，不是最新版（Y）」；最新版沒有版號時括號整個省略、不補字，選單也只寫「執行中 X（不是最新版）」。沙盒那一版沒有記到版號時，X 寫成 "an unrecorded release" / 「未記錄的版本」——那是事實，不是代填的版號。 | [mine，review round 1–2] |
| D14 | 「同不同、缺不缺」只有一條規則 `tooling/external.py:drift()`：turn 的限制（`confine_to_mounted`）、給模型的句子（`describe_running`）、選單（`tools_routes._row`）都讀它，不再各寫一份。`ExternalTools.versions()` 也由 `mounts()` 推導，turn 與非 turn 的紀錄是同一個 builder。 | [mine，review round 2] |

**不處理**：manifest 的版本號變了但 bundle 沒換（手改 manifest、混用兩次 build 的產物）。這時實際 sha =
最新 sha，偵測不到；`tooling/builder.py` 在同一次 build 產出兩者，正常發版不會發生。

## 機制

### 資料

- `tooling/external.py` 新增 `MountedTool(sha: str, version: str)`。
- `_SandboxAddress` 加 `tools: dict[str, _Mounted] | None = None`。`None` = 不知道（這個欄位出現前寫的舊列）；
  `{}` = 確定沒掛任何第三方工具——建立時解析失敗也寫 `{}`，因為那個沙盒確實什麼都沒掛。只加有預設值的欄位：
  舊列照常解碼，沒有 `Schema` 升版、沒有回填。
- `IAddressStore.claim` / `swap` 多收 `tools`，與 `handle_id` **同一次寫入**（同一列、同一個 CAS），所以
  讀到的掛載資訊一定屬於讀到的那個位址。讀取是 `published(item_id) -> Published{handle, tools}`，一次讀出兩者。

### 寫入：建立沙盒時

```
turn:      resolve_item_tools → ExternalTools{shas, provenance}
           → ctx.sandbox_spec.tools = shas，ctx 同時帶 versions
           → registry.ensure_handle(session, tools=MountedTool 表)
非 turn:   registry._declared_tools → tools_for(item) → MountedTool 表（同一個 resolve）
兩者 →     _acquire: sandbox.create(spec.tools = {name: sha})
                    → address.claim/swap(item, handle, tools=MountedTool 表)
                    → session.tools = MountedTool 表
收斂到別人的沙盒 → session.tools = address.published(item).tools（與位址同一次讀；以前是 None）
搶輸 CAS        → 只有讀到的位址仍是贏家時才採用它的紀錄，否則 None
```

### 讀取：每一輪、每次開選單

`registry.mounted_tools(item)`（整段限時 `mounted_probe_timeout_s`，任何錯誤或逾時 → `None`，D12）：

1. 本 pod 的 session 有 handle 且 `session.tools` 已知 → http 時先探活，活著才用它（別的 pod 關掉的沙盒不能
   一直被這個 pod 回報）；
2. 否則有位址：`published(item)` 一次讀出位址與紀錄，`_alive` 探活；活著才回它的 `tools`，死了回 `None`
   （下一次建的就是最新版，D7）；
3. 其餘 → `None`。

### 呈現

- `turn_context._external_tools`：對每個已解析的工具，掛載 sha ≠ 最新 sha → 該 `PackageInfo` 的 `version`
  換成掛載的版本，`latest_version` 填最新版（`None` = 一致；`""` = 不一致但最新版沒有版號）。
  `describe_command` 在 `latest_version` 不是 `None` 時加一句（英文，prompt 一律英文，D13）。掛載未知 → 不動（D4）。
- `GET /a/{slug}/items/{id}/tools`：每列加 `running_version`（只在不一致時有值）與 `not_in_sandbox`（D11）；
  整體加 `update_needs_close: bool` 與 `can_close: bool`（D9 的共用閘；出錯時當 `False`）。
- `ToolsChecklist`：不一致的列另起一行顯示「執行中 v1 · 最新 v2」（可換行——放在原本那行會被省略號截掉，390px 實測只剩「Runnin…」）；`ToolsPickerModal` 在 `update_needs_close` 時於
  清單上方放一行說明，`can_close` 時加「關閉沙盒以更新」按鈕（`myResourcesApi.closeEnvironment`），成功後
  重抓選單、沙盒狀態與資源用量；失敗只在按鈕旁說一次（`silentError`）。

## Phases

每一步 `/tdd`：先寫在未修程式碼上變紅、走真路徑的測試；一個 phase 一個 commit。

- **P1** `MountedTool` + `_SandboxAddress.tools` + store 的 `claim`/`swap(tools=)` 與讀取（P6 起是 `published()`）；舊列（無欄位）
  讀成 `None`。
- **P2** registry：`_acquire` / `ensure_handle` / `rebuild` 帶 `MountedTool` 表、寫進位址列；`tools_for`
  回 `MountedTool` 表；收斂時讀列；`mounted_tools(item)`。turn 的兩個 lambda 傳版本。
- **P3** turn：`_external_tools` 改用 `mounted_tools`，套 D2；`describe_command` 加句。
- **P4** 選單：API 欄位 + 共用的關閉閘 + 前端列文字與按鈕 + i18n。
- **P5** `docs/migrations.md`（行為改變、無開關：選單與 AI 說的版本改成沙盒實際的；跨 pod 的已掛載判斷）。
- **P6**（review round 1）D11–D13；session 也探活；位址與紀錄一次讀；選單的錯誤只說一次並刷新資源。
- **P7**（review round 2）D12 的「本 pod 紀錄照用」；D14 一條規則；turn 只為模型會拿到的工具查；其他關閉入口也刷新
  選單；最新版沒有版號時不補字；文件與測試名稱的用詞。

## 已知、這次不修

- 被限制住的那一輪（沙盒少了某個工具）若在第一次 exec 前或中途遇到沙盒被回收而重建，新的沙盒照這一輪限制後的
  清單建，所以也沒有那個工具，並記進紀錄；要到下一次關閉才補上。以前本 pod 建的沙盒就是這樣，D10 讓別的 pod
  也看得到紀錄，範圍變大；需要「沙盒被回收」與「限制」剛好同一輪，罕見（review round 2，B）。

## 驗證

- P1–P3：registry 探針那一組情境改寫成測試（turn 在 A、關在 B……），斷言 AI 收到的工具說明句子。
- P4：前端單元測試（不一致 / 一致 / 未知 / 沒有權限 / 未記錄版號 / 沙盒裡沒有 / 關閉失敗 / 關閉保留未存的切換）＋
  真 Chromium 1280／390 截圖看過。
- prod：發一版工具 → 不關沙盒開選單，看到「執行中 舊 · 最新 新」與按鈕；問 AI 版本，它說沙盒裡實際的版本並提到關閉沙盒；
  按按鈕後再開選單，不一致消失。
