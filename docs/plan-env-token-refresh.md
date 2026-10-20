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
| D4 | 換失敗**照樣執行**（工具拿到現有的值）；讓人知道要重新登入：有人在場→畫面上提示，無人在場→通知替誰跑的人。存著的 refresh token 只有在部署方的程式明確說「無效」時才清掉，網路不通、對方 5xx 都不清 | 〔user〕選 1（推薦）；「有人在場的提示」怎麼畫見 **O1** |
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
- 有人在場：**O1**。

## 未決

- **O1（要 user 決定）**：`refresh()` 回 `RefreshRejected`、而且有人在場時，畫面上怎麼提示。#892 的登入卡片是
  **AI 呼叫 `request_env` 工具的回傳**才畫得出來（`agent/env_request.py:37`；`plan-env-request-card.md` N1「由 AI
  決定」），平台自己不能插入。兩個做法：
  - (a) 平台在那一輪的對話裡放一則平台訊息「{系統} 登入已過期，請重新登入」，附連到 `/my-env` 的連結——沿用
    現成的 `role="notice"` 訊息（#624 的「較早的訊息不再送給模型」用的就是它：`chat_send.py:697`、`turns.py:110`）。
    一定出現，但不是那張有登入按鈕的卡片。
  - (b) 平台在那一輪告訴 AI「{名稱} 的登入已過期，請呼叫 `request_env`」，由 AI 叫出 #892 的登入卡片（按
    「登入 X」→ 登入頁 → 存檔 → 重試，全是現成的）。是 user 記得的那張卡片，但要 AI 照做才會出現。
  user 先選了 (a)，接著說「印象中我們做過這個東西」，指的是 #892 的卡片——兩者不同，還沒定。

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
| P6 | 無人在場失敗 → `notify()`；有人在場 → 照 O1 | 去重；值從不出現在通知裡 |
| P7 | 文件：`extending-the-platform.md`（部署方怎麼寫 refresh）、runbook 條目、plan-personal-env「不做」那條標推翻、design-history | `mkdocs build --strict`；`tests/docs/test_docs_index.py` |
| P8 | live check：替身 provider（token 幾十秒就過期、refresh 會 rotation）＋真 app；錄 demo（過期 → 自動換 → 工具拿到新值） | 對照 master：同一流程在 master 上工具拿到過期值 |
