# token 快過期時，平台自己換新的

> grill 2026-10-19。每條決定標了來源：**〔user〕** = user 逐題定案；**〔預設〕** = 我自己定、列出來給推翻的
> 實作層決定。推翻 [plan-personal-env](plan-personal-env.md)「不做」裡的「過期偵測」那一條（只有那一條）。

## 問題

環境變數裡放了 refresh token，但平台只會**存值**：「我的環境變數」、Private、Shared 都是字串，原封不動交給
工具；部署方的系統登入（`IEnvProvider.resolve`，`api/env_provider.py`）只在有人按「登入」的那一刻跑一次。
token 過期之後，要嘛人去重新登入，要嘛工具自己拿 refresh token 去換——而 user 不希望這件事在 MCP／工具裡做
〔user〕「我們不希望這個工作在mcp裡面完成」。工具拿到的環境變數是唯讀的，就算它換到新的也寫不回去；遇到
「refresh token 用一次就作廢」（rotation）的系統，第一次換完存著的那份就失效了。

## 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 換 token 的方法寫在部署方的系統登入（`IEnvProvider`）裡：多一個**可選的** refresh 步驟。平台決定「什麼時候換、換完存到哪」，怎麼換由部署方的程式決定；工具完全不碰 | 〔user〕選 1（推薦） |
| D2 | **要用的時候才換**：平台在組工具的環境變數之前，發現快過期就先換；沒人用就不換 | 〔user〕選 1（推薦） |
| D3 | 換到的新值**從哪讀到就存回哪**：我的環境變數／這個 item 的 Private（自己填的那份）／Shared。Shared 由平台代為寫入，不看當下替誰跑的人有沒有 `write_meta` | 〔user〕選 1（推薦） |
| D4 | 換失敗**照樣執行**（工具拿到現有的值）；讓人知道要重新登入：有人在場→畫面上提示，無人在場→通知替誰跑的人。存著的 refresh token 只有在部署方的程式明確說「無效」時才清掉，網路不通、對方 5xx 都不清 | 〔user〕選 1（推薦） |
| D6 | 有人在場時，平台在那一輪的對話裡放一則**一定會出現**的平台訊息「{系統} 登入已過期，請重新登入」，裡面直接放「登入 X」按鈕，打開 #892 的同一個登入頁；不經過 AI、不含重試按鈕（見「有人在場時的提示」） | 〔user〕「你的建議是什麼？」→ 建議 (a) 加按鈕 →「可以，照這樣寫進計畫」 |
| D5 | 平台是**介面**，看不到部署方怎麼實作，所以「refresh 失敗」由**介面明文規定的回報方式**決定：平台自己呼叫 `refresh()`，看它的結果——正常回傳＝成功；raise `RefreshRejected`＝refresh token 確定無效（只有人重新登入能解決）；其他任何例外或逾時＝暫時失敗。部署方沒照做（全部丟一般例外）時，平台一律當暫時失敗：不清、不提示，等工具自己失敗再走 #892 的 `request_env` 卡片 | 〔user〕「你怎麼知道登入失敗？你只是 interface…除非你要規定實做方登入失敗要怎樣」→「可以，照這樣寫進計畫」 |

## 機制

### 部署方多寫的兩個方法（D1）

`IEnvProvider` 多兩個方法，**都有預設**，沒寫的部署一切照舊〔預設〕：

```python
def needs_refresh(self, values: dict[str, str]) -> bool:
    """values = 這個 provider 產出的那些名稱、此刻要交給工具的值（包含 refresh token）。
    便宜、不打網路：例如讀 JWT 的 exp，剩不到 N 分鐘就回 True。預設 False。"""

async def refresh(self, values: dict[str, str]) -> dict[str, str]:
    """拿 refresh token 換新的一組值（可以包含新的 refresh token）。預設 raise NotImplementedError。
    回報方式是契約（D5），平台只看這三種結果："""
```

| `refresh()` 的結果 | 意思 | 平台怎麼做 |
|---|---|---|
| 正常回傳一組值 | 換成功 | 寫回原本那一層（D3），交給工具 |
| raise `RefreshRejected` | refresh token 確定無效（被撤銷、過期），只有人重新登入能解決 | 清掉那一層裡的 refresh token，照樣執行，提示／通知重新登入（D4） |
| raise 其他任何例外，或逾時 | 暫時失敗（網路不通、對方 5xx） | 什麼都不清，照樣執行，下次要用時再試 |

平台**不知道**的是：工具拿 token 去打外部系統時有沒有被拒絕——那是工具自己的事，照舊走 #892（工具 exit 3 →
AI 呼叫 `request_env` → 登入卡片）。`extending-the-platform.md` 要寫清楚：確定 refresh token 無效時一定要
raise `RefreshRejected`，否則人要等到工具失敗才會被告知。

- **「快過期」由部署方判斷**〔預設〕：平台不另外記過期時間——手打進去的 token、按登入拿到的 token 都適用，
  也不必在 `PersonalEnv`／`PrivateEnv` 加欄位。
- refresh token 本身也要列在 `produces` 裡，平台才知道那組值屬於哪個 provider〔預設〕。
- 與 `resolve` 同一條信任邊界：值不記 log、不放進例外訊息（`env_provider.py` 的既有規定）。

### 掛在哪裡（D2）

所有「替誰跑」的入口都經過兩個非同步函式之一（`api/private_env.py`）：`private_layer`（有人在場：聊天
`chat_send.py:491`、頁面 callTool `wui_routes.py:311`）和 `unattended_layer`（無人在場：goal、reclaim 重跑
`chat_send.py:1094`、workflow 節點 `workflow_exec.py:501`，排程經由 workflow_exec）。在兩者之後、`resolve`
之前加一步 `refresh_if_due(...)`〔預設〕：

1. 對每個有 `refresh` 的 provider，取它 `produces` 的名稱，用**後端的 `layer_of(name)`**（`resolve_env` 的
   順序，回傳 `shared | private | personal | seam | service | none`）找出每個名稱此刻從哪一層來。
2. 組出「此刻要交給工具的值」，問 `needs_refresh`；False 就結束（不打網路）。
3. True → 拿鎖（下一節）→ **重讀**那一層（別人可能剛換完，換完就不必再換）→ 還需要才 `refresh` → 寫回同一層
   （D3）→ 放鎖。
4. 回傳換好的各層，`resolve` 照舊。

- **WUI build 不碰**：它以「沒有人」的身分跑（`wui_routes.py:373`），沒有個人層可換（plan-wui-viewer-login D11）。
- **sub-agent 不另外換**：繼承父 turn 的 ctx（`agent/context.py:716`）。
- **自動帶入值（seam）、服務帳號（service）不寫回**〔預設〕：seam 是部署的 `IRequestEnv` 每個 request
  重寫的，service 沒有列可寫；這兩層贏的名稱照舊交出去，不換。

`layer_of` 是 `resolve_env` 順序的**第二份實作**，所以用既有的對照表 `tests/fixtures/env_layers_cases.json`
當 oracle 做 parity 測試（和前端 `layerInUse` 同一份），不靠手動保持一致。

### 同時刷新只做一次（D3 的前提）〔預設〕

兩個執行（或兩顆 pod）同時發現快過期，若都去換：rotation 的系統裡第二個用的是剛作廢的 refresh token，
換失敗，還可能把好的值蓋掉。所以每個 `(層, 擁有者, provider)` 一把**跨 pod 的鎖**：

- 一列 `_RefreshClaim`（spec.apply 之後註冊，沒有 auto-CRUD），用 specstar 的 CAS（`expected_etag`，
  同 `sandbox_address.py:236`、`turn_claims.py`）搶；帶過期時間（refresh 的時限 + 餘裕），持有者死掉時不會永遠卡住。
- 沒搶到的那一方**等**（有上限），然後重讀那一層直接用新的；等到上限還沒好，就照 D4 用現有的值執行。
- 擁有者：personal → user；private → (user, item)；shared → item。

### 寫回（D3）〔預設〕

- personal：`PrivateEnvStore.replace_personal`（保留沒改的名稱的 `updated`）。
- private（自己填的那份）：`PrivateEnvStore.replace`。
- shared：item 的 `env_vars` 目前只能經由 item PATCH（`write_meta`）改，沒有伺服器端的寫入函式——新增一個，
  用 `rm.modify(..., expected_etag=...)` 只改 refresh 回來的那幾個名稱，revision 的 actor 記成平台（例如
  `system:env-refresh`），看得出不是人改的。
- 只寫 `refresh` 回傳的名稱；回傳裡出現 `produces` 以外的名稱也照寫（與 `resolve` 的「原樣寫入」一致）。

### 失敗（D4）

- `RefreshRejected(names)`：清掉那一層裡**部署方列出的那幾個名稱**（例如 refresh token 和隨它作廢的 access
  token），照樣執行。名稱由部署方說，因為只有它知道哪個變數是 refresh token〔預設〕；列了 `produces` 以外的名稱
  就不理那幾個，免得部署方的程式清掉別人的值。
- 其他例外、逾時、等鎖逾時：什麼都不清，照樣執行。
- 無人在場：用既有的 `notify()`（`notifications.py:23`）通知替誰跑的人，連到 `/my-env`；同一人同一 provider
  每天去重一次（同 `workflow_exec.notify_failure` 的 dedup 形狀）〔預設〕。
- 有人在場：照 D6，在那一輪的對話裡放一則平台訊息，附「登入 X」按鈕。

### 有人在場時的提示（D6）

`refresh()` raise `RefreshRejected`、而且有人在場（聊天、頁面按鈕；頁面按鈕實際做法見 A4）時：

- 平台在那一輪的對話裡放一則**平台訊息**「{登入方式的 label} 登入已過期，請重新登入」，存進對話紀錄（重新整理後
  還在），沿用現成的 `role="notice"` 訊息（`turns.py:110`；#624 用它寫「較早的訊息不再送給模型」，
  `chat_send.py:697`）。不經過 AI，一定出現。
- 訊息裡直接放「**登入 X**」按鈕，按下去打開 #892 的同一個登入頁元件（`plan-env-request-card.md` N6），存檔照
  #750／#892 D10 的規則（按「儲存」才存）。訊息要帶一個機器讀得到的登入方式 id，前端才知道開哪一個登入頁
  〔預設：照 `request_env` 結果尾端的標記做法〕。
- **沒有「重試」按鈕**：#892 卡片的重試綁在 AI 的那次工具呼叫上（D8），平台訊息沒有對應的呼叫可綁；登入存檔後
  再跟 AI 說一次。這一輪的工具照 D4 拿到舊值，可能失敗；下一輪就是新值。
- 同一輪、同一個登入方式只放一則。

為什麼不是 #892 的卡片：那張卡片只有 **AI 呼叫 `request_env`** 才畫得出來（`agent/env_request.py:37`；
`plan-env-request-card.md` N1「由 AI 決定」）。平台在這裡已經**確定**登入失效，再交給模型決定要不要出卡片，
小模型可能不照做，人就什麼都看不到。

依據（NN/g〈Error-Message Guidelines〉，https://www.nngroup.com/articles/error-message-guidelines/）：
「Display the error message close to the error's source」——放在發生的那一輪；「Merely stating the problem is
also not enough; offer some potential remedies」「guess the correct action and let users pick it」——平台已經知道是
哪個登入方式失效，直接給那個登入按鈕，而不是一個要人自己找的連結。

## 不做

- 背景定時換（D2 否決）：要放在 worker（#804），且會替每個人一直打外部系統。
- 標準 OAuth2 的內建設定版（D1 否決）。
- 由工具在 `env.json` 宣告怎麼 refresh（D1 否決，與 #750 的信任邊界相反）。
- 寫回 seam、service 兩層（見上）。
- 終端機的 `exec` 帶 env（今天就不帶，plan-personal-env「不做」）。

## Phases

| Phase | 內容 | 驗收 |
|---|---|---|
| P1 | `IEnvProvider.needs_refresh` / `refresh` / `RefreshRejected(names)`（D5 的回報契約），預設不改任何行為 | 沒寫的 provider：`needs_refresh` 為 False、`refresh` 從不被呼叫 |
| P2 | 後端 `layer_of(name, …)`，與 `resolve_env` 同一份順序 | parity：`env_layers_cases.json` 每一列 `layer_of` 與 `resolve_env` 選中的層相同；突變順序表任一格會紅 |
| P3 | `_RefreshClaim` 跨 pod 鎖（CAS + 過期） | 兩個並行搶同一把只有一個拿到；持有者不放鎖，過期後別人拿得到 |
| P4 | `refresh_if_due`：判斷 → 拿鎖 → 重讀 → refresh → 寫回同一層 → 放鎖；失敗照 D4 | 每一層各一條寫回測試；rotation 情境兩個並行只換一次；`RefreshRejected` 清、其他例外不清 |
| P5 | providers 一路傳到 `ChatSendService`、`WorkflowExecutor`、wui 路由，掛在 `private_layer` / `unattended_layer` 之後 | 每個入口（聊天、callTool、goal、reclaim、workflow 節點、排程）各一條測試：快過期時工具拿到新值；WUI build 不呼叫 refresh |
| P6 | 無人在場失敗 → `notify()`；有人在場 → D6 的平台訊息＋「登入 X」按鈕（前端：notice 帶登入方式 id 時畫按鈕，打開 #892 的登入頁） | 去重；值從不出現在通知或訊息裡；按鈕打開的是對的那個登入方式 |
| P7 | 文件：`extending-the-platform.md`（部署方怎麼寫 refresh）、**runbook 條目（內容見下方「runbook 條目必須寫的事」）**、design-history | `mkdocs build --strict`；`tests/docs/test_docs_index.py`；runbook 條目逐條對照下方清單 |
| P8 | live check：替身 provider（token 幾十秒就過期、refresh 會 rotation）＋真 app；錄 demo（過期 → 自動換 → 工具拿到新值） | 對照 master：同一流程在 master 上工具拿到過期值 |

## 施工時的決定（as-built，PR #985）〔預設〕

| # | 計畫寫的 | 實際 | 為什麼 |
|---|---|---|---|
| A1 | P6 一個 phase 做完提示 | P6 後端（通知＋對話訊息）、P7 前端（按鈕）、P8 文件、P9 第一輪 review 的修正、P10 live check | 照 flat 編號：拆出去的工作用下一個整數 |
| A2 | 有人在場的提示沿用 `role="notice"` | 新 role `sign_in`，`Message` 多 `sign_in_provider`、`sign_in_layer` 兩個選填欄位（舊資料照讀，沒有 `Schema` 升版） | `already_noticed` 把任何 `notice` 當成「#624 已經提示過」，沿用會讓 context 上限的提示不再出現；`history_items` 只送 user／assistant／tool／summary 和使用者取消的標記，新 role 不進模型，`_replayed` 也不算它 |
| A3 | 「登入 X」打開 #892 的登入頁 | 都打開 #892 的登入頁，存到哪依 token 原本所在的層：我的環境變數 → 存回我的環境變數（`EnvLoginModal saveTo="personal"`，走 `PERSONAL_ENV_WRITES` 佇列）；這個 item 的 Private 或 Shared → 存進這個 item 的 Private | D3「從哪讀就寫回哪」：#892 的登入頁只會存進這個 item 的 Private，會讓「我的環境變數」裡失效的 token 只修好一個 item。Shared：平台已經把失效的名稱從 Shared 清掉，而 Shared 的值只會在最先被讀時才拿得到（shared_first，或 private_first 而下面都沒有值），所以清掉後下一層就是 Private；Shared 要 `write_meta` 才能改，Private 每個人都能改。第一輪 review：原本 Shared 打開環境變數面板，標著「登入 X」卻不是登入，沒有 `write_meta` 的人會卡在改不了的面板 |
| A4 | 有人在場＝聊天、頁面按鈕 | 頁面按鈕的工具呼叫用通知告訴按的人（和沒人在場一樣，`in_chat=False`） | 頁面沒有對話可以放提示。第一輪 review：原本什麼都不說，靠頁面的登入列（`identityState`），但那只在工具把這個變數宣告為必填時才出現 |
| A5 | 時限、等待 | 平台的 `refresh` 上限 30 秒（`REFRESH_TIMEOUT_S`）、等別人換 30 秒（`WAIT_FOR_PEER_S`）、鎖 45 秒過期（`CLAIM_TTL_MS`）；不做成設定 | 部署方本來就要自己限時；平台的上限只防卡死 |
| A6 | 通知 | 標題 "Sign in to {label} again"；token 在我的環境變數 → 連到 `/my-env`，每人每個登入方式每天一次；在 item 的 Private／Shared → 連到那個 item，每人每個登入方式每個 item 每天一次（dedup key 帶 UTC 日期）；英文，和既有的 workflow 失敗通知一致 | 去重避免每輪都通知；連結照 D3 指到 token 所在的地方 |
| A7 | 排程一條測試 | 排程的節點走 workflow_exec 的同一條路（`_headless_env`），由 workflow 節點的測試涵蓋 | 同一個呼叫點 |
| A9 | 寫回 | 寫回是 patch：寫的當下重讀那一層，只有這次換的名稱還是送出去的那組值時，才設新值（或清掉失效的）；其他名稱照現況。Shared 在 item 的 CAS 迴圈裡做 | 第一輪 review：原本把換之前讀的整層寫回去，換的這 30 秒內別人改的值會被蓋掉，登出的值會被寫回來 |
| A10 | 等別人換 | 對方放掉鎖就再讀一次、沒寫（換失敗）就不再等；對方清掉任一個名稱（登入失效）就也回報失效。部署方 `RefreshRejected` 列的名稱一個都不在這一層時當暫時失敗；沒列全時留下的名稱照留，並寫 warning log | 第一輪 review：原本只看值有沒有變，對方失敗時要白等 30 秒；Shared 的 token 失效時，同時送出的其他人看不到提示 |
| A11 | 「最後一則」的判斷 | 平台在一輪中途寫的訊息（`sign_in`、#624 的 `notice`）不算（`turnTail`，在 `logFromMessages` 裡）；跨 pod 的 store poll 看到它時走 `reconcile`，也是經 `logFromMessages` 判斷，所以同一個判準 | 第一輪 review：`sign_in` 寫在問題下面、這輪開跑之前，重新整理或另一個 pod 的人會看到這輪「已經結束」 |
| A8 | 兩個並行只換一次的測試 | 以事件排序、不靠時間；鎖本身由「預先佔住鎖」的測試釘住（讓鎖永遠拿得到時它會紅） | in-memory 後端的 create-if-absent 不是原子的，靠時間的版本在負載下偶爾兩個都拿到 |

## runbook 條目必須寫的事（P7；as-built 為 P8）

〔user〕2026-10-20「這些要放在最後的 migration.md 喔」——部署方在這個 PR 之後要做的事，一條不漏寫進
`docs/migrations.md` 的條目（照 CLAUDE.md：每個動作寫指令、WHEN、WHY、不做的症狀）：

1. **在系統登入程式（`server.env_providers` 列的 `IEnvProvider` 實作）加 `needs_refresh` 和 `refresh`**，附範例：
   - `produces` 要包含 refresh token 的變數名（例如 `ERP_REFRESH_TOKEN`），平台才知道那組值歸誰管；
   - `needs_refresh` 不打網路、要快（例如讀 JWT 的 `exp`）；
   - refresh token 確定無效時一定要 `raise RefreshRejected(names)`；其他錯誤照常丟例外——不照做的症狀：平台當暫時失敗，
     不提示重新登入，人要等到工具失敗才知道（D5）；
   - 值不寫進 log 或例外訊息；自己限制等待時間。
   - 還沒有登入程式的部署：要先寫完整的一個（`id`、`label`、`produces`、`inputs`、`resolve`）再加這兩個方法。
2. **拿掉工具／MCP 裡自己 refresh 的程式**，工具只讀 access token。
3. **設定不用改**，照常 build、部署；不加這兩個方法 = 一切照舊。
4. **確認做完**（`rollout 後`）：
   - 讓某人的 token 快過期（或在測試環境把有效期限調短），請 AI 跑用到它的工具 → 工具拿到新 token，「我的環境變數」的值被換新；
   - 把 refresh token 改成無效值再跑 → 對話出現「{系統} 登入已過期，請重新登入」和「登入 {系統}」按鈕；排程的話，替誰跑的人收到通知。
5. **要知道的後果（D3）**：token 放在某個 item 的 **Shared** 共用值時，平台換到新值會**直接改寫那個 item 的共用值**，
   不看當下的人有沒有改設定的權限；修改紀錄標成平台寫的。
