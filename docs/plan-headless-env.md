# Plan：沒有人按送出的 turn 也拿得到環境變數（service account 走 `IRequestEnv` 接縫）

> **狀態:計畫,待點頭。未實作。**

接續 #714(從 request 組 env)與 #788(build 不注入)。這份計畫補的是 #714 當初**刻意留下**的
一個洞:**沒有 request 的 turn 什麼都拿不到**。

## 現況與問題

`IRequestEnv.env_for(request, *, user_id, item_id)`(`src/workspace_app/api/request_env.py`)是
部署自己插的 impl:從那次 request 的 cookie / header 換出這個人的憑證,只活那一輪 turn、不落地。
它只在**有 request** 的入口被問:

| 入口 | 有 request? | 今天拿到什麼 |
|---|---|---|
| 聊天送出(`chat_send.send`) | ✅ | `{**request_env, **item_env}` |
| WUI 頁面 `callTool`(`wui_routes.py:502`) | ✅ | 同上 |
| goal driver 自己續的回合(#615,`chat_send._goal_followup` → `send(request=None)`) | ❌ | `{}` + item_env |
| workflow 每一個 agent node(`workflow_exec.drive_turn` → `build_workflow_turn`) | ❌ | item_env 而已 |
| ↳ 其中 item 排程(#805 `user_schedule_sweep`)、event trigger、人按 `POST …/run` 的整條 run | ❌ | 同上 |

`chat_send._resolve_request_env`(`chat_send.py:379`):`if self._request_env is None or request is None: return {}`。
`turn_context._common`(`turn_context.py:725`):`user_env={**(request_env or {}), **facts.env_vars}`,
而 `build_workflow_turn`(`turn_context.py:896`)根本沒有 `request_env` 這個參數。

**問題:**一個要打自家 DB / 內部工具的 tool,在聊天裡好好的,排到半夜就沒有憑證。#714 的答案是
「放 item 的 `env_vars`」——但那格是 service account token 的**手抄本**:每個 item 各抄一份、
會過期、過期了每個 item 各自壞。prod **已經有 service account 機制**,只是平台沒有一條路去問它。

**#714 當時不接 workflow 的理由**(`docs/extending-the-platform.md`「隨按下送出的那個人而變的變數」):
> workflow 整條沒有:它會續跑、會被排程和上傳事件重跑,「第一步有、第二步沒有」是 UI 上看不出來的差別。
> goal driver 自己續的那些回合同樣沒有——沒有請求就沒有身分,而且沒有任何存下來的東西可以繼承。

兩句都對,而且**本計畫不推翻它們**:沒有 request 就不該假裝有 request。要補的是**第二個問題**——
沒有人在的時候,turn 該以**誰**的身分跑?答案不是「繼承上一個人」,是「部署說了算」。

## 決定的做法

**接縫多一個方法,平台自己不認得 service account 這個詞。**

```python
class IRequestEnv(abc.ABC):
    @abc.abstractmethod
    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]: ...

    async def env_without_request(self, *, user_id: str, item_id: str) -> dict[str, str]:
        """What a turn with NO request behind it gets: a scheduled workflow node,
        a goal-driver continuation, an event-triggered run. ``user_id`` is the
        user the run was captured as (the item owner for an item schedule, the
        goal's setter for a goal turn). Default: nothing — today's behaviour."""
        return {}
```

- **預設回 `{}`** ⇒ 現有部署的 impl 不改一行、行為完全不變。沒設 `server.request_env` 的部署
  也完全不變(接縫不存在就沒有這條路)。
- **impl 決定政策**:回全域 service account、回 per-user 的 service account、或看 `item_id`
  決定給不給。平台只負責「沒有 request 的 turn 去問這個方法」。這正是 `docs/plan-wui.md`
  「兩條憑證線」那節的原則——**平台給一般化的機制,應用決定政策**——只是那張表「排程跑」那格
  從 ❌ 變成 ✅。
- **不落地。** 跟 `env_for` 一樣只活一輪、不進 item、不進任何儲存。過期 / refresh 是 impl 的事
  (它每一輪都會被問一次,自己決定要不要快取)。

**兩個沒有 request 的入口都接:**

1. **goal driver**:`_resolve_request_env` 的 `request is None` 分支改成問
   `env_without_request(user_id=author, item_id=…)`。`author` 在這條路上已經是 `current.set_by`
   (`chat_send.py:561`),不必新增任何身分解析。
2. **workflow 每一個 agent node**:`WorkflowExecutor.drive_turn` 手上已有 `captured_user`
   (`workflow_exec.py:141`);在 `build_workflow_turn` 前問一次,經新增的 `caller_env=` 參數
   帶進 `_common`。`captured_user` 對 item 排程是 item owner(`user_schedule_sweep.py:353`)、
   對 trigger 是 `t.acting_user`、對人按 `run` 是按的那個人(`workflow_routes.py:455`)。

**整條 workflow 一律走 headless,包含人按 `POST …/run` 起的那條。** 這正好回答 #714 當初的
顧慮:「第一步有、第二步沒有」的差別之所以可怕,是因為兩步身分不同又看不出來;現在每一步、
每一次重跑都是**同一個來源**(headless),差別消失了。人按 run 時**不**用他的 request env,
因為那條 run 之後會被排程重跑,重跑時他不在——一開始就用 headless 身分跑,結果才可重現。

**其餘 #714 / #788 的規則原樣保留:**

- 合併方向不變:`{**headless_env, **item_env}`,item 的設定蓋過、不提示。
- 注入點不變:只在 package tool 派送(`tooling/registry.py:422`);builtin `exec` 沒有(#673);
  `wui_build` 沒有(#788,build 產出是共享成品)。**`env_without_request` 也不進 build**——
  build 不論誰觸發都會落到 `dist/`,和有沒有 request 無關。
- 失敗 = 這一輪不跑,impl 的例外文字不外流(只進 server log)。

## 已鎖定的決策(來自對話,不再開)

| # | 決定 | 來源 |
|---|---|---|
| 1 | request 帶來的值**不存**進 item `env_vars`(user 一開始問「能不能存」,理由講完後改選 service account) | 「不過他會過期 也是很麻煩 應該要有一個service account注入才對」 |
| 2 | 走**部署插 impl** 的接縫,不做平台內建的 service account 概念 | 同 #714「要讓我能夠插入 impl 才對」;`plan-wui.md` 兩條憑證線 |
| 3 | 預設 `{}`,對現有 impl 零破壞 | 沒設就完全不存在,同 #714 |
| 4 | `user_id` 傳 run 的 `captured_user` / goal 的 `set_by`,**平台不決定**它是 per-user 還是全域 | impl 的政策 |
| 5 | 進實作前先寫 plan、停下來等點頭 | 「Ok 進入實作前先寫plan」 |

## 知情取捨(不需再決策,但要寫進 PR)

- **這條改變了 #714「workflow 整條不接」的定案**,理由寫在上面:原顧慮是「兩步身分不一致」,
  headless 來源讓每一步一致,顧慮不再成立。PR body 要引 #714 那段原文並說明為何不再適用。
- **同一個 chat 裡,人送出的那輪和 goal driver 續的那輪身分不同**(前者 `env_for`、後者
  `env_without_request`)。這是需求本身:沒有人在就用 service account。⚠️ 但 `plan-wui.md` 寫的
  「個人 token 在不在就是有沒有人在的訊號」,**在 impl 對兩個方法回同一個變數名時會消失**。
  這是 impl 的選擇;文件要提醒:想保留那個訊號,就給不同的名字(或在 headless 那邊多回一個標記)。
- **goal driver 那條的失敗仍然是安靜的。** 今天 `_goal_followup` 任何例外都只 `logger.exception`
  後停掉(`chat_send.py:584`,「the goal would stop either way, but nothing would say why」),
  headless env 失敗會走同一條——**本計畫不新增 thread 通知**,那是 #615 自己的設計缺口,
  不該混進來;但要在 PR body 明講,並開 issue。workflow 那條**不是**安靜的:失敗變成 run 的
  `ERROR`(`driver.py:75`),看得見。
- **`ITokenService`(LLM 那條憑證線)不動。** 兩條憑證線的表只改 `IRequestEnv` 那列。
- **人按 `run` 起的 workflow 不拿他的 request env**(理由在上面)。如果將來有人要「這一次用我的
  身分跑」,那是一個新需求,不是本計畫漏掉。
- **不做 TTL / refresh / 快取。** 每輪問一次,快取與否是 impl 的事——平台快取等於平台決定
  過期政策,而它不知道對面的 token 活多久。

## Phases

每個 phase 一個 commit;每條測試先對未改的程式碼驗紅。

### Phase 1 — 接縫多一個方法(預設 `{}`)

- `request_env.py`:`IRequestEnv.env_without_request(*, user_id, item_id) -> dict[str, str]`,
  非抽象、預設回 `{}`。docstring 說明 `user_id` 是誰、什麼情況會被問、預設等於今天。
- 測試(RED:今天是 `AttributeError`):只實作 `env_for` 的子類可以實例化,`env_without_request`
  回 `{}`。

### Phase 2 — 兩個沒有 request 的入口接上

- `chat_send._resolve_request_env`:`request is None` 且有接縫 ⇒ 問 `env_without_request`;
  失敗走**同一個** try/except(500 `request_env_failed`,不外流 impl 文字)。docstring 改寫——
  「a turn without a person behind it inherits nothing」那句不再成立。
- `turn_context`:`_common` / `build_chat_turn` 的 `request_env` 參數改名 **`caller_env`**
  (它現在裝的是「呼叫端帶來的 env」,可能來自 request、可能來自 headless 來源;叫 `request_env`
  會在 workflow 那條說謊);`build_workflow_turn` 新增同名參數。`turn_context.py:716–724` 那段
  「`request_env` is empty for every turn with no request behind it … which is the whole of what
  those turns inherit: nothing」的註解改寫。
- `WorkflowExecutor`:建構子收 `request_env: IRequestEnv | None`(`app.py:2051` 傳同一個實例);
  `drive_turn` 在 `build_workflow_turn` 前問 `env_without_request(user_id=captured_user, item_id=item_id)`。
- 測試(每條 RED):
  - goal driver:接縫的 `env_without_request` 回 `{"SA_TOKEN": "x"}`,用 `app.state.chat_send.send(request=None)`
    (#714 覆蓋率那節同一條路)⇒ 建出的 ctx `user_env` 含它;**而且 `env_for` 沒被叫**
    (那個替身的 `env_for` 直接 `raise`,對照組)。
  - workflow:`drive_turn(captured_user="alice")` ⇒ 接縫被叫時 `user_id == "alice"`,ctx `user_env`
    含 headless 值;item `env_vars` 同名時 item 贏。
  - 人送出(有 request):`env_for` 被叫、`env_without_request` **沒**被叫(對「兩個都問」的突變體紅)。
  - 人按 `POST …/run`:那條 run 的 node 走 `env_without_request`,**不是** `env_for`(對「有 request
    就用 request」的突變體紅)。
  - 三個被測替身的身分要不同(owner / 按 run 的人 / goal setter),否則測不出傳的是哪一個。

### Phase 3 — 注入邊界不變的守衛

- 既有的「`wui_build` 不拿 request env」測試改成同時斷言**也不拿 headless env**(對「build 也問
  `env_without_request`」的突變體紅)。
- 既有的「builtin `exec` 沒有 env」測試(#673)保持;確認 headless 值也沒有經 `exec` 進去。

### Phase 4 — 失敗看得見、impl 文字不外流

- `WorkflowExecutor.drive_turn`:接縫丟例外 ⇒ `logger.exception` + `raise StepFailed("headless env source failed")`
  (固定字串;`driver.py:75` 會把 `str(exc)` 寫進 run record,所以**不能**讓 impl 的例外直接上去)。
- 測試(RED):impl `raise RuntimeError("token=hunter2")` ⇒ run 終態 `ERROR`,`result["error"]`
  不含 `hunter2`,run 的事件流也不含;server log 有 traceback。
- goal driver:既有處理(log + 目標不推進);測試斷言 thread 裡沒有 `hunter2`。

### Phase 5 — 文件對齊

- `docs/extending-the-platform.md`「隨按下送出的那個人而變的變數」:
  「只有聊天送出、以及 WUI 頁面的 `callTool` 有」那條改寫;「workflow 整條沒有」「goal driver 同樣沒有」
  兩點改成「走 `env_without_request`」並引本計畫;加 impl 範例(回 service account token);
  加「想保留『有沒有人在』的訊號就用不同變數名」的提醒。
- `docs/plan-wui.md`「兩條憑證線」表:`IRequestEnv` 那列的「排程跑」從 ❌ 改 ✅(`env_without_request`)。
- `docs/design-history.md` 加一列。
- `docs/extending-the-platform.md` 裡的範例 impl 逐字可用(#714 的保證探針延伸到新方法)。

## 驗收

- 沒設 `server.request_env`:OpenAPI、`AgentToolContext`、落地對話**逐位元相同**(#714 差分探針重跑)。
- 設了但 impl 只有 `env_for`:每一條 headless 路徑拿到的 `user_env` 和今天**一樣**(`{}` + item_env)。
- 設了且 impl 有 `env_without_request`:item 排程跑的 node、goal driver 續的回合、人按 run 的 node,
  tool 派送時 `os.environ` 看得到 impl 回的名字(`SANDBOX_USER_ENV_KEYS` 那條探針延伸);
  `exec` 看不到、`wui_build` 看不到。
- 值不進 DB / 不進 SSE / 不進 log / 不進 run record(#714 保證探針延伸;每個探針先用突變體證明不是空的)。
- `ruff check` / `ruff format --check` / `ty check` 綠;targeted 測試綠;CI 綠;至少一輪對抗式 review。

## 不做

- 平台內建 service account 的概念、設定或儲存。
- 把 request 的值存進 item `env_vars`(已否決)。
- goal driver 失敗的 thread 通知(另開 issue)。
- `ITokenService` 那條線。
- TTL / refresh / 快取。
