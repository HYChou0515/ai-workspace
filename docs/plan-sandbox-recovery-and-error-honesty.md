# 沙盒被收走之後平台不會復原,然後說錯原因

> 這份計畫處理一整類缺陷,不是幾個獨立的 bug:**平台失去它的 sandbox 之後不會自己站起來,
> 而它接著回報的原因是錯的。** 錯誤的分類導致錯誤的動作(換模型、不重試),
> 錯誤的動作再製造更多症狀。
>
> ⚠️ **證據等級,先講清楚。** 每一條「程式碼是這樣寫的」都附行號、可在 repo 裡 30 秒查證。
> **觸發時機來自使用者的實地回報**(「好發在跑 CI/CD、sandbox host `rollout restart` 之後」、
> 「沒做事情 sandbox 就回歸了」),那是這份計畫裡最強的證據 —— 比我推的都強。
> 而**沒有任何一條在正式環境上量過**,我看不到 prod。同一天我對 prod 做過四次診斷推測
> (429 預算、窗口太長、TTFT 門檻太低、token 估算太慢),**四次都被使用者的實際數據否定**;
> 那四次的共通點正是它們都是對 prod 的猜測。下面的 P0–P2 不是那一類 ——
> 它們是程式碼的結構事實,與 prod 怎麼設定無關。

---

## 1. 主要觸發:`rollout restart`,不是零星的競態

使用者回報這**好發於 CI/CD 之後的 `rollout restart`**。那不是偶發競態,是**一次大規模同時失效**:

```
host pod 收到 SIGTERM / PreStop 打 /drain
   ↓
那些 pod 上「所有」sandbox 同時消失
   ↓
每個 app pod 的快取 handle、每一列 _SandboxAddress 同時變成死的
   ↓
所有 app pod 幾乎同時發現並同時重建(thundering herd)
   ↓
部分請求打到「還在 endpoint 清單裡、但已在 draining」的 pod
```

### host 的 drain 契約寫完了一半

sandbox-host **有** drain,而且行為明確 —— drain 中對 `create` 回
**503 `{"error": "draining"}`**(`sandbox-host/src/sandbox_host/app.py:612-615`),
`__main__.py:100-103` 把 SIGTERM 接上 `start_draining`,PreStop hook 打 `POST /drain`。

**但 app 這邊沒有實作另一半。** `draining` 這個字串在整個 `src/workspace_app/` **一次都沒出現**,
而 `sandbox/http_client.py:46-49` 的對應表只有兩個鍵:

```python
_ERRORS = {"SandboxNotFound": SandboxNotFound, "FileNotFoundError": FileNotFoundError}
```

`create` 則是(`http_client.py:351-372`):

```python
resp = await self._client.post(f"{self._base_url}/sandboxes", json={...})
resp.raise_for_status()      # ← 503 draining 在這裡變成裸的 httpx.HTTPStatusError
```

於是那個 503 **既不是 `SandboxBusy` 也不是 `SandboxNotFound`**,連 `api/app.py:1245-1270`
那兩個「503 + `Retry-After`」的 handler 都接不到,直接變成 **500**。

`draining` 的語意本來就是「**別找我,去找別的 pod**」—— 它是整個系統裡最適合重試的訊號,
而我們把它當成不明錯誤。

---

## 2. 次要觸發:兩個回收器的門檻差 16 倍

| 誰 | 門檻 | 它自己宣稱的用途 |
|---|---|---|
| app `registry.kill_idle` | `idle_timeout` — `registry.py:823` 寫「**8 hours by default**」 | 正常回收 |
| **host `reap_idle`** | `sandbox-host/config.py:48` 預設 **1800 秒 = 30 分鐘** | `config.py:22`:「reaps sandboxes **orphaned by an app-pod crash**」 |

host 那個回收器是為了清掉**「app pod 崩潰後留下的孤兒」**而存在的,**但它分辨不出孤兒和
「還有人用、只是剛好在發呆」** —— 兩者在它眼裡都是 30 分鐘沒有請求。

而它的活動時鐘只被**打到該 sandbox 的 HTTP 請求**重置
(`sandbox-host/app.py:470-478` 的 `_track_activity` middleware,只認 `/sandboxes/<id>/…`)。
**單純開著網頁不會產生那種請求** —— 讀檔案樹、看聊天記錄都走 FileStore 或根本不出網路。
所以「發呆 30 分鐘 → 被當成孤兒殺掉」是每天都會發生的事,這正是使用者說的
「我沒做事情 sandbox 就回歸了」。

`registry.py:245-250` 的註解其實早就寫下這件事 ——
「the host reaped the sandbox out from under us (**30-min idle TTL** / pod death)」——
**但只在檔案那條路上做了防禦。**

> **操作員現在就能轉的旋鈕(不必等這份計畫)**:`SANDBOX_HOST_IDLE_TTL`。
> 調到接近 app 的 8 小時,兩邊就不再打架;代價是真正的孤兒多佔一段時間。
> 這是部署決定,計畫不替操作員拍板。

---

## 3. 失去 handle 之後:檔案會復原,`exec` 不會

**重建機制存在而且完整。** `registry.py:207` `rebuild_io_handle`:

> Force a fresh live handle after a file op hit `SandboxNotFound` … `_acquire` converges on
> another pod's live address **or rebuilds from the durable archive and republishes (CAS)**.

`ensure_handle`(`registry.py:229-259`)也會**主動 probe** 快取的 handle,死了就重新取得;
`files/facade.py:291` 的 `_warm` 在**每一個檔案操作**都 probe,註解寫明
「this probe is **also the RECOVERY trigger**」。

**但 agent 的 `exec` 兩個呼叫點都沒有接。** `agent/tools.py:120-123`:

```python
handle = await ctx.context.ensure_sandbox()
result = await ctx.context.sandbox.exec(handle, cmd, on_output=ctx.context.on_exec_output)
```

第二行**沒有任何 `try`/`except`**。`ensure_sandbox()` 只在**取 handle 的那一刻**保護你,
之後 handle 被整個 turn 快取在 `session.handle` 裡。

```
檔案工具  撞到 SandboxNotFound → 重建 → 繼續做事        ✅
exec     撞到 SandboxNotFound → 例外原樣拋給 SDK        ❌
```

### 那串「看起來像 JWT」的東西

每一個沙盒例外都**直接拿 handle 當訊息**(`http_client.py:271` `:279` `:516` `:519`,
以及 `:323` 的 `message = body.get("detail") or handle.id`),而 handle 是
(`http_client.py:52`):

```python
def _encode_handle(pod_url: str, remote_id: str) -> str:
    raw = json.dumps({"u": pod_url, "r": remote_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()
```

所以 agent 收到的是 `An error occurred while running the tool. Please try again.
Error: <base64>` —— 它照字面「try again」,打的還是同一個死 handle(session 快取著),
再拿到一串 base64。**而那串 base64 解開是 pod 的內網位址,它被寫進 agent 的 context 和
transcript。**

---

## 4. 平台在斷言它沒有證據的原因

| 使用者看到 | 出處 | 那行實際知道的 |
|---|---|---|
| 「模型忙碌中,請再稍候」 | `web/src/components/TurnStatus.tsx:266` | **只有碼錶** —— `sec > 15`,不看任何訊號 |
| `no first token ⇒ the model is busy` | `failover/model.py:11` | 只有「8 秒內沒有第一個 token」 |
| 「All available models are busy」 | `api/turns.py:319` | cause chain 裡**可能明明掛著 429** |
| `Error: <base64>` | `http_client.py:271` 等 | 沙盒不見了 |

第一項的原始碼註解自己就寫著 `never claim content volume or guess a cause; "busy" is true
regardless` —— 作者本意是挑一個**不指涉原因**的填充詞。但中文的「模型忙碌中」讀起來是一句
**斷言**,而使用者的原話是「**我們的模型不應該忙碌了**」。他是對的:那行從來沒問過模型。

---

## 5. Phases

每個 phase 的驗收方式一致:**先寫對現況會紅的測試 → 修 → 突變驗證(把修法刪掉,測試必須
再變紅)→ 推 CI**。紅的那次輸出會貼出來,不是只宣稱「測過了」。

### P0 — 認得 `draining`(rollout 的那一半契約)

- `draining` 進 `_ERRORS`,對應 **`SandboxBusy`** —— 既有語意裡最正確的一個:
  「它活著、只是現在不能服務你」。`SandboxBusy` 已經有退避重試(`IoRetryPolicy`),
  在 API 邊界已經是 503 + `Retry-After: 2`。
- `create` 走同一套判讀,不再讓 `raise_for_status()` 把它變成裸例外。
- **為什麼排第一**:它是 rollout 那條路上最短的修法(一個字典鍵 + 一個判讀),
  而 rollout 是使用者說的主要觸發時機。

### P1 — `exec` 在沙盒不見時重建並重跑

- `SandboxNotFound` → 走既有的重建路徑取得新 handle → **重跑一次**;
  第二次仍失敗就回一句人看得懂的話。
- `SandboxBusy` → **不重建**,退避重試。#492 已定調「rebuilding a merely-busy sandbox」
  是錯的,`registry._alive`(`:360-375`)也刻意把 busy 讀成「活著」。
- 涵蓋 `agent/tools.py` 裡**每一個** `sandbox.exec` 呼叫點(`:123`、`:317`),
  不是只修使用者貼的那一個。
- ⚠️ `agent/python_env.py` 的三個 exec(`:145` `:159` `:177`)**不在這個 phase**:
  它們跑在 `ensure_sandbox` 的 `_wake` 鎖裡面,在那裡呼叫重建會**死鎖**。

### P2 — 沙盒錯誤講人話

- 訊息帶:哪個 item、busy / gone / draining、host 說了什麼。
- **`handle.id` 不再進訊息**,連帶止住 pod 內網位址流進 agent context。
- log 保留 handle(操作員需要它),但那是 log,不是模型的輸入。

### P3 — 三處停止宣稱「模型忙」

- `TurnStatus`:只知道秒數就說秒數,不說原因。
- `turns.py` 的 `_BUSY_MESSAGE`:cause chain 上有 429 就說限流
  (`failover/model.py:210` 是**刻意** `raise ... from (rate_limited or last)`,
  註解自己說上游要靠走這條 chain 分辨),並且**搬進 i18n** ——
  它現在是 Python 裡的硬寫英文,而整個產品說繁中。
- TTFT 逾時:說「沒有在 N 秒內開始回應」,不說「忙」。

### P4 — 讓原因可分辨,並記錄

- 每次 hold / degrade 印出**原因字串**。現況:切換那一刻有印
  (`failover/observe.py` 的 `make_switch_logger`),但**每次 hold 那行沒有**
  (`model.py:154` 只印 model/hold/wait);chain 用完印的是 `last`(`:204` `:276`),
  **而同段註解自己說 `last` 常是最沒資訊的那個**。
- **不動換模型的策略。** 要不要改成不換,得先有這裡的資料讓操作員看到真實比例。

---

## 6. 明確不做的事

- **不改 failover 的切換策略**(`ttft_timeout_s` 調多少、429 要不要立刻切換)。
  那是部署參數與產品決定,需要 prod 的數字。
- **不改任何 idle TTL 的預設值。** 第 2 節那個 16 倍落差是真的,但兩個數字各自都有理由,
  對齊它們是操作員的決定;計畫只負責讓落差被看見、並讓落差發生時系統能站起來。
- **不修 `_warm` 的 `.ready` 缺口。** `files/facade.py:291` 的 probe 問的是
  `exists(handle, "/")` —— **只問「在不在」,沒問「還原完了沒」**,所以讀取可能落在
  一個已建立但 `sync.restore` 還沒跑完的 sandbox 上,看到半個工作區。
  這在 rollout 的大規模重建下會被放大。**已知、未修、不在這份計畫的範圍** ——
  它要動的是重建與就緒的交握,比這裡每一項都大。單獨處理。
- **不猜 litellm 的 429 body/header 格式。** 要分辨 budget / RPM / 併發得先看到真實回應;
  P4 把已經在手上的字串印出來就是為了拿到它,而不是先寫一個猜出來的 regex。

---

## 7. 操作員現在就能做的三件事

1. **`SANDBOX_HOST_IDLE_TTL`** —— 見第 2 節。今天唯一能立刻讓「發呆回來就壞掉」停止的東西。
2. **`WORKSPACE_PERF_TRACE=1`** —— `api/perf_trace.py` 已在 repo 裡,它量的正是
   「是 hub 還是 llm service」:`db` 是同步 specstar 往返(在 async route 上**握著
   event loop**),`other` 大 + `inflight>1` 是在等 loop 而非等自己,
   loop-lag watchdog 是不依賴任何請求自我記帳的獨立證人。
3. **`grep "rate-limited (hold"`** —— `model.py:154` 本來就印了等待秒數。
   1/2/4/8/16 是我們在盲目退避(對方沒給 `Retry-After`);
   大數字是對方講了、我們照單全收(`rate_limit_wait_s` 對 stated 值**沒有上限**,
   只有 7200 秒的總池擋著)。
