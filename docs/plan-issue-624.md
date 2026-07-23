# #624 — context 上限:偵測、處理、告知

**問題一句話**:我們從來不知道模型能吃多少,卻用兩個憑空的常數決定要忘記多少,而且忘記的時候不告訴任何人。

使用者長期回報的「問一問 AI 就忘記前面」,根因在此。

---

## 1. 現狀(全部已查證,附證據)

### 1.1 偵測:不存在

`grep -r "v1/models|get_model_info|max_input_tokens|max_model_len" src/workspace_app/` → **零個命中**。

我們從未詢問任何 endpoint「你能吃多少」。

### 1.2 兩個憑空常數決定記憶

`api/turns.py::history_items` 實際做的兩件事:

```python
if max_messages:  msgs = msgs[-max_messages:]        # config: 40
if max_tokens:    msgs = _fit_token_budget(msgs, …)  # config: 24000
```

兩個值寫死在 `config/schema.py::HistorySettings`,與模型真實上限無關。

### 1.3 系統提示與工具規格完全不在預算內

`litellm_runner.py:271` 直接送 `config.system_prompt`。實測一個 RCA item 的系統提示為 **74,222 字元**,外加 31 個工具 schema。預算只裁歷史,看不到這塊。

### 1.4 估算器用錯:同 repo 有兩個,聊天用了對中文錯的那個

| 位置 | 算法 | 同一段中文對話 |
|---|---|---|
| `api/turns.py::_est_tokens`(**聊天在用**) | `chars // 4` | 2,435 |
| `kb/tokens.py::count_tokens`(#88 為繁中語料而寫) | CJK 1 字 1 token + 其餘 /4 | 7,446 |
| **模型實際回報** | — | **8,755** |

聊天路徑**低估 3.6 倍**。

### 1.5 裁切完全靜默

`_fit_token_budget` 結尾只有 `return kept` — 不回報丟了幾則、不記 log、不發事件。**資訊在源頭就被扔掉**,不是「有但沒顯示」。

### 1.6 沒有任何摘要 / 壓縮功能

全 repo 的 `summariz*` 命中都在 KB(code wiki 大綱、VLM 描述、merge、eval),沒有一個為了把對話塞進 context。

### 1.7 400 的處理是反效果

- `_should_retry` **只看**「有沒有吐字」與「試了幾次」,**不看錯誤類型** → 同一個過長 prompt 重送 **3 次**
- `diagnose_error` 落到 catch-all,回饋一句 "The previous attempt failed… Try again." **附加到那個已經太長的 prompt 上,使它更長**
- 400 不在 `failover/retry.py:46` 的 transient 集合 `{408,409,429,500,502,503,504}` → 不換模型(這點正確)
- 使用者最後看到 `RunError("giving up after 3 attempts: …")` 的原始英文錯誤,沒有任何可行動指引
- 使用者訊息在 turn 前已持久化,失敗又寫入一則 `role="error"` → **歷史更長 → 下一輪更容易再失敗**

### 1.8 文件宣告了一個從未驗證的前提

`configs/config.example.yaml`:

> *「Default sized for the bundled local qwen3 (~32K ctx); raise it for big-context hosted models.」*

而 `num_ctx` / `OLLAMA_CONTEXT_LENGTH` 在 `configs/`、`docs/`、`kubernetes/` **grep 零命中**。照文件裝起來的部署,拿到的是一個每輪被截斷的 agent,而文件讓人以為一切正常。

---

## 2. 已量測的衝擊

### 2.1 我們自己砍掉多少(provider 無關,production 一定發生)

每個工作回合 = 4 則訊息(問 → agent 說明 → 工具輸出 → 結論):

| 情境 | 從第幾回合開始丟 | 穩定後 AI 看得到 |
|---|---|---|
| 輕量(工具輸出 1.2K 字元) | 第 **11** 回合 | 最後 40 則 ≈ **10 個回合** |
| 常見(工具輸出 30K 字元 = `exec` 上限) | 第 **4** 回合 | 最後 13 則 ≈ **3 個回合** |

第 60 回合時第二種情境:240 則只送出 13 則,**丟掉 95%**。

**且與 vLLM 開多大無關** — 這是我們在送出前自己套的天花板,對方的 context size 目前是一個從未被用到的數字。

### 2.2 Provider 失敗模式不同(dev 實測 / production 待驗)

| 服務層 | 超長時 | 證據 |
|---|---|---|
| **Ollama** | **靜默截斷**,不報錯 | 實測 `prompt_eval_count` = 3,983 / 8,755;同對話 `num_ctx=40960` 則完整看到並答對 |
| **vLLM** | raise 驗證錯誤(`invalid_request_error`) | 官方文件 `_token_len_check`;**未在你們機器實測** |

Ollama 被截斷時**不會說「我不知道」,會編一個看似合理的答案**(實測:問開頭的專案代號,答「TSMC-2023-001」,實際是 ORCHID-7)。

### 2.3 上限的可得性

| 來源 | 結果 |
|---|---|
| `litellm.get_model_info("gpt-4o")` | 128,000 ✅ |
| `litellm.get_model_info("ollama/qwen3:14b")` | 40,960 ✅ |
| **`litellm.get_model_info("openai/qwen3-14b")`** | **查不到 ❌**(= production 的形狀) |
| Ollama 的 OpenAI 相容 `/v1/models` | 只有 `{id, object, created, owned_by}`,**無長度欄位** |

→ 對自架 vLLM,內建表無效;`max_model_len` 是 vLLM 的擴充,非 OpenAI 標準。

### 2.4 反推所需的儀器早已存在但未被使用

`litellm_runner.py::_exact_usage` 已在收 `usage.input_tokens`(模型實際吃進去的 token),並已流入 `AgentMetrics`。**但從未與我們的估算做比對** — 只拿去顯示 UI 的 ↑ 數字。

---

## 3. 鎖定的決策

| 決策 | 內容 |
|---|---|
| 預設行為 | **不預先裁切**。上限多大就用多大,不自斷腳筋 |
| 上限單位 | 可設定;能拿到真值就用 token 真值,否則用估算 + 餘裕 |
| 超長處理 | 400 → **砍一半重試**(而非現在的重送同一份 3 次) |
| 裁切告知 | **一定要說一聲**。轉折點在對話裡留可見標記 |
| 摘要 / 壓縮 | **不做**(v2)。撞到天花板時誠實告知並建議開新對話 |
| `truncate_prompt_tokens` | **不用**。那是把靜默截斷外包給 vLLM,正是要消滅的東西 |
| config 與現實衝突 | config 決定行為;但當 400 證明 config 是錯的,**以現實為準 + 警告 operator** |

---

## 4. 分支樹(每條都要有活路)

```python
# ── 送出前 ───────────────────────────────────────────
limit = (config_limit                  # ① operator 說了算
         or learned_limit(model, url)  # ② 之前從流量學到的
         or litellm_limit(model)       # ③ 內建表
         or None)                      # ④ 還不知道

if limit is None:
    送出全部                           # 預設不卡 → 靠回應學
else:
    est = count_tokens_cjk(訊息)
    if est + 系統提示 + 工具 + 回覆保留 <= limit:
        送出全部
    else:
        裁到裝得下 + 在對話裡說一聲

# ── 收到回應後(所有分支都做)─────────────────────────
P = usage.prompt_tokens                # 已經在收了
if P is not None and P << est:
    learned_limit = P                  # 它偷偷截斷了,P ≈ 真實視窗
    警告 + 在對話裡說一聲
elif P is not None:
    校準估算器誤差(選配)

# ── 收到 400 ─────────────────────────────────────────
if 是長度類 400:
    learned_limit = parse(錯誤訊息)     # vLLM 訊息內含上限
    if 只剩 1 則訊息:
        fail loud:「這則訊息本身就超過上限」+ 指出是哪則
    else:
        砍一半 → 重試 → 在對話裡說一聲
else:
    直接失敗,不重試
```

**每個 leaf 的活路:**

| 分支 | 活路 |
|---|---|
| 不知道上限 | 照送 → 從 usage 或 400 學到 → 下一輪就知道 |
| 估算器不準(15%) | 只在明確超過時裁 + 留餘裕;寧可被拒也不預先亂剪 |
| 被靜默截斷 | usage 反推抓到 → 學到上限 + 告知,不會一直錯下去 |
| 砍半砍到底 | 剩 1 則仍爆 → fail loud 指出是哪一則,不無限迴圈 |
| 非長度 400 | 立刻失敗,不浪費 3 次重試 |
| **config 設錯(設太大)** | 400 證明後**以現實為準 + 警告**,逃生門不會反鎖 |

---

## 5. Phases

> 順序原則:**不依賴 operator 提供資訊的先做**;`/tokenize` 探測放最後,因為沒有它整套仍然可用。

### P1 — 純函式:上限解析器 + 準確估算(零行為改變)

**Goal.** `resolve_limit()`(四層階梯,回傳值 + 來源標記)與換用 CJK-aware 計數,**都以純函式落地並測試,先不接進裁切路徑**。

- 分開落地的理由:單獨換估算器會讓同一段中文算出 3.6 倍 token → 24,000 的預算提早觸頂 → **健忘變更嚴重**。估算器與預算推導必須同時生效(P2)。
- 測試:四層階梯各自命中/落空;CJK 估算對照實測值。

### P2 — 送出前分支:預設不卡 + 裁切要說一聲

**Goal.** 兩個憑空常數退休,改由 `limit` 推導;`limit` 未知時不裁切;真的裁切時在對話留可見標記。

- `max_messages` 不再當守門員(記憶由 token 決定,不是「第幾則」)
- 預算 = `limit − 系統提示 − 工具 schema − 回覆保留 − 餘裕`
- 標記沿用 #613 的 `role=` 標記機制(FE 會渲染、`history_items` 天生不收非 user/assistant/tool 的 role)
- 只在**轉折點**響一次,不是每輪都響(否則第二輪就變壁紙)
- 裁切要**整塊裁**,不要每輪掉一則 — 前綴每輪變動會讓 vLLM 的 prefix cache 全失效

### P3 — 事後偵測:usage 反推靜默截斷

**Goal.** 每輪比對「我們送的估算 E」與「provider 回報的 `usage.prompt_tokens` P」;`P << E` 判定為靜默截斷 → 學到上限、警告、在對話裡說一聲。

- 這是讓 Ollama 那類靜默截斷**現形**的唯一機制
- 需要防誤判(短 prompt 本來就 P 小):要求差距顯著且可重複觀測才下結論
- 學到的值快取於 `(model, base_url)`,可被新觀測推翻(不是永久)

### P4 — 400 的正確處理

**Goal.** 分類 400、從錯誤訊息學上限、砍半重試、觸底 fail loud、非長度 400 不重試。

- 改 `_should_retry`:目前不看錯誤類型,是「重送同一份 3 次」的根源
- 改 `diagnose_error`:長度類要有自己的分支,不能落到那句會讓 prompt 變更長的 catch-all
- config 設太大時以現實為準 + 警告

### P5 — `/tokenize` 探測(需 operator 提供資訊)

**Goal.** 能拿到真實 token 數時就用真值,取代估算。

- 啟動時探測;404 / 逾時 / 任何失敗 → 靜靜退回 P1–P4 的路徑(不影響可用性)
- **阻擋中**:需確認 vLLM `/tokenize` 的實際回傳格式與 `max_model_len` 值(見 §7)
- 需實測每輪多一次 HTTP 往返的延遲代價

### P6 — 文件與部署指引

**Goal.** 讓文件不再宣告未經驗證的前提。

- `configs/config.example.yaml`:移除「~32K ctx」這個假設句;寫明新旋鈕;寫明 Ollama 預設 4096 與 `OLLAMA_CONTEXT_LENGTH`(含 VRAM 代價)
- 各 provider 的失敗模式對照表(誰報錯、誰靜默截斷)

---

## 6. 非目標

- **對話摘要 / 壓縮** — v2。撞到天花板時誠實告知並建議開新對話即可
- **vLLM `truncate_prompt_tokens`** — 刻意不用(靜默截斷)
- **調整 `exec` 輸出上限**(30,000 字元)— 另一個議題,雖然它是吃掉預算的主因
- **縮減系統提示**(#480 把全部工具 schema 寫進提示,佔 74,222 字元)— 值得另案評估,不在此範圍

---

## 7. 待決(需 operator 提供,擋住 P5)

1. **你們 vLLM 的 `max_model_len` 是多少?**
2. **`/tokenize` 端點的實際回傳格式**:
   ```bash
   curl -s http://<vllm>/tokenize -H 'Content-Type: application/json' \
     -d '{"model":"<模型名>","prompt":"這批晶圓的量測資料"}' | jq
   ```
3. **vLLM 有沒有開 prefix caching?**(影響 P2「整塊裁」的價值評估)
4. **production 的 `observability.llm_log` 有沒有開?**(有的話可以直接量真實 prompt 大小,把 §2.1 從模擬換成實測)

**沒有這些也能做完 P1–P4 與 P6** — 這正是「先做沒回或 404 那條路」的設計意圖。

---

## 8. 風險

| 風險 | 緩解 |
|---|---|
| 估算器仍有 15% 誤差 | 保留餘裕;真正的準確度靠 P3 的 usage 反推校準 / P5 的真值 |
| 學到錯誤的上限 | 需顯著差距 + 可重複觀測才下結論;可被 config 覆寫、可被新觀測推翻 |
| 裁切破壞 prefix cache | 整塊裁,讓前綴多輪穩定 |
| unknown 期間第一輪未校準 | 第一輪就會偵測到並告知,不會持續錯下去 |
| P2 上線後使用者開始看到裁切通知 | **這是預期的** — 它讓一直存在但被隱藏的天花板變得可見 |
