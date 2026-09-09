# 錯誤訊息指錯原因,而 exec 撞到死掉的沙盒不會重建

> 這份計畫處理的是**一整類**缺陷,不是四個獨立的 bug:平台在**斷言它沒有證據的原因**,
> 而錯誤的分類接著導致錯誤的動作(換模型、不重試),錯誤的動作又製造更多症狀。
>
> ⚠️ **關於證據等級,先講清楚**:這份計畫裡的每一條「程式碼是這樣寫的」都可以在 repo 裡
> 30 秒查證,行號都附上了。但**沒有任何一條在正式環境上量過** —— 我看不到 prod。
> 使用者回報的症狀(沙盒死掉、一直換模型、等好幾分鐘)與這些程式碼路徑**相符**,
> 但「相符」不是「證實」。同一天我對 prod 做過四次診斷推測(429 上限、窗口太長、
> TTFT 門檻太低、token 估算太慢),**四次都被使用者的實際數據否定**。
> 那四次的共通點正是它們都是對 prod 的猜測。
> 下面的 P1/P2 不同 —— 它們是程式碼的結構事實,與 prod 長什麼樣無關。
> P3/P4 動的是措辭與可觀測性,同樣不需要先知道 prod 的答案。

---

## 1. 共同的病灶

使用者一天之內回報四個看似無關的症狀。查下來,**每一個都是他在看一句指錯原因的訊息**:

| 使用者看到 | 出處 | 那行程式碼實際知道的 |
|---|---|---|
| 「模型忙碌中,請再稍候」 | `web/src/components/TurnStatus.tsx:266` | **只有碼錶** —— `sec > 15`,不看任何訊號 |
| `no first token ⇒ the model is busy` | `src/workspace_app/failover/model.py:11` | 只有「8 秒內沒有第一個 token」 |
| 「All available models are busy」 | `src/workspace_app/api/turns.py:319` | cause chain 裡**可能明明掛著 429** |
| `Error: <一串 base64>` | `src/workspace_app/sandbox/http_client.py:271` 等 | 沙盒不見了 |

第一項的原始碼註解自己就寫著 `never claim content volume or guess a cause; "busy" is true
regardless` —— 作者的本意是挑一個**不指涉原因**的填充詞。但中文的「模型忙碌中」讀起來是一句
**斷言**,而使用者的原話是:**「我們的模型不應該忙碌了」**。他是對的:那行從來沒有問過模型。

---

## 2. 最嚴重的一條:exec 撞到死掉的沙盒,什麼都不做

**重建機制是存在的,而且寫得很完整。** `src/workspace_app/api/registry.py:207`:

```python
async def rebuild_io_handle(self, investigation_id: str) -> SandboxHandle:
    """Force a fresh live handle after a file op hit ``SandboxNotFound`` (the
    published sandbox was reaped/gone). ... ``_acquire`` converges on another
    pod's live address or rebuilds from the durable archive and republishes."""
```

`ensure_handle` 的註解也明講:「a busy host retries, **a gone one triggers `rebuild_io_handle`**」。

**但那條路只接在檔案操作上。agent 的 `exec` 沒有接。**

`src/workspace_app/agent/tools.py:120-123`:

```python
handle = await ctx.context.ensure_sandbox()
result = await ctx.context.sandbox.exec(handle, cmd, on_output=ctx.context.on_exec_output)
```

**第二行沒有任何 `try`/`except`。**

`ensure_sandbox()` 只在**取 handle 的那一刻**保護你(`registry._alive` 會 probe、死了就重建)。
handle 拿到手之後就被整個 turn 快取在 `session.handle` 裡。所以沙盒在 turn 進行中死掉時:

```
檔案工具  撞到 SandboxNotFound → rebuild_io_handle → 重建 → 繼續做事      ✅
exec     撞到 SandboxNotFound → 例外原樣拋給 SDK                          ❌
```

### 迴圈是怎麼閉合的

```
沙盒死掉(被回收 / pod 不見 / host rollout)
   ↓
exec 拋出 SandboxNotFound(handle.id)          ← 訊息就是那個 handle
   ↓
Agents SDK 包成 "An error occurred while running the tool.
                 Please try again. Error: <base64 handle>"
   ↓
agent 照字面「try again」→ 打的還是同一個死 handle(session 快取著)
   ↓
再一串 base64 → 迴圈
```

而那串「看起來像 JWT」的東西是 `sandbox/http_client.py:52`:

```python
def _encode_handle(pod_url: str, remote_id: str) -> str:
    raw = json.dumps({"u": pod_url, "r": remote_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()
```

每一個沙盒例外都**直接拿它當訊息**(`:271` `:279` `:516` `:519`,以及 `:323` 的
`message = body.get("detail") or handle.id`)。所以錯誤訊息不但沒有資訊,還把
**pod 的內網位址寫進 agent 的 context 和 transcript**。

### 而且前端也沒有接

後端把 `SandboxBusy` / `SandboxNotFound` 對應成 **503 + `Retry-After`**
(`api/app.py:1245-1270`),註解寫明「it is transient and worth retrying」、
「the next attempt re-creates it lazily, **which makes this a retry, not a 404**」。

**但 `sandbox_busy` / `sandbox_gone` 這兩個字串在整個 `web/src` 只出現在一個測試檔裡**
(`api/myResources.test.ts`),產品程式碼一次都沒有,`http.ts` 也沒有讀 `Retry-After`。

也就是說:後端說「這是暫時的,再試一次」,然後把它交給一個不會再試的客戶端。

---

## 3. Phases

每個 phase 的驗收方式一致:**先寫對現況會紅的測試 → 修 → 突變驗證(把修法刪掉,測試必須
變紅)→ 推 CI**。紅的那次輸出會貼出來,不是只宣稱「測過了」。

### P1 — exec 在沙盒死掉時重建並重跑

- `SandboxNotFound` → 呼叫既有的重建路徑取得新 handle → **重跑一次**。只重建一次;
  第二次仍失敗就回一句人看得懂的話。
- `SandboxBusy` → **不重建**,退避重試。#492 已經定調:「rebuilding a merely-busy
  sandbox」是錯的,`registry._alive` 也刻意把 busy 視為「活著」。
- 涵蓋 `agent/tools.py` 裡**每一個**呼叫 `sandbox.exec` 的地方(`:123`、`:317`),
  不是只修使用者貼的那一個。

### P2 — 沙盒錯誤講人話

- 例外訊息帶:哪個 item、是 busy 還是 gone、host 說了什麼。
- **`handle.id` 不再進訊息**,連帶止住 pod 內網位址被寫進 agent context。
- log 保留 handle(操作員需要它),但那是 log,不是模型的輸入。

### P3 — 三處停止宣稱「模型忙」

- `TurnStatus`:只知道秒數就說秒數,不說原因。
- `turns.py` 的 `_BUSY_MESSAGE`:cause chain 上有 429 就說限流(那顆例外
  `failover/model.py:210` 是**刻意** `raise ... from (rate_limited or last)` 串上去的,
  註解自己說上游要靠走這條 chain 分辨),並且**把字串搬進 i18n** —— 它現在是
  Python 裡的硬寫英文,整個產品說繁中。
- TTFT 逾時:說「沒有在 N 秒內開始回應」,不說「忙」。

### P4 — 讓 TTFT 逾時變成可分辨的原因,並記錄

- 每次 hold / degrade 都印出**原因字串**。現況:切換的那一刻有印
  (`failover/observe.py` 的 `make_switch_logger`),但**每一次 hold 那行沒有**
  (`model.py:154` 只印 model/hold/wait),而那是跨數十次的常見分支;
  chain 用完印的是 `last`(`:204` `:276`),**而同段註解自己說 `last` 常是最沒資訊的那個** ——
  真正的 429 在 `rate_limited` 變數裡,只被串進 cause 卻沒 log。
- **不動換模型的策略。** 換模型解決不了「我們給的時間不夠對方算完」,但要不要改成不換,
  得先有 P3/P4 的資料讓操作員看到真實比例。今天已經有一次「自己替使用者拍板」的教訓,
  不重複。

---

## 4. 明確不做的事

- **不改 failover 的切換策略**(`ttft_timeout_s` 該調多少、429 要不要立刻切換)。
  那是部署參數與產品決定,需要 prod 的數字。
- **不動 `連線中斷,這裡可能少了一段` 的跨 pod 序號邏輯**。那條橫幅在多 pod 下
  可能是誤報(`useChatSession.tsx` 的註解自己寫著
  「often enough that the test fails on a turn nothing was lost from」),
  但修它要動跨 pod 的 broadcast 編號,範圍比這份計畫大。
- **不猜 litellm 的 429 body/header 格式**。要分辨 budget / RPM / 併發,得先看到真實回應;
  P4 把已經在手上的字串印出來,就是為了拿到它,而不是先寫一個猜出來的 regex。

---

## 5. 操作員可以自己做的兩件事(與這份計畫無關,現在就能做)

1. **`WORKSPACE_PERF_TRACE=1`** —— `api/perf_trace.py` 已經在 repo 裡,它量的正是
   「是 hub 還是 llm service」:`db` 是同步 specstar 往返(在 async route 上**握著
   event loop**),`other` 大 + `inflight>1` 是在等 loop 而不是等自己,
   loop-lag watchdog 是不依賴任何請求自我記帳的獨立證人。
2. **`grep "rate-limited (hold"`** —— `model.py:154` 本來就印了等待秒數。
   看到 1/2/4/8/16 是我們在盲目退避(對方沒給 `Retry-After`);
   看到大數字是對方講了、我們照單全收(`rate_limit_wait_s` 對 stated 值**沒有上限**,
   只有 7200 秒的總池擋著)。
