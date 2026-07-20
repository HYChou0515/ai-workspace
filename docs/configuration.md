# 設定指南（Configuration Guide）

> 這頁教你**怎麼調 `config.yaml`**：三種覆蓋機制、最小起步、「我想做 X 要改哪個 key」速查、
> 三條部署階梯，以及 **sandbox** 與**環境變數**兩個最容易踩雷的區塊。
>
> 分工：本頁講「**跑起來後用 YAML 調哪顆旋鈕**」；[部署指南](deployment.md) 講「**用程式/factory
> 換掉整塊實作**」（寫你自己的 Sandbox / FileStore / Runner）；[`configs/config.example.yaml`](https://github.com/HYChou0515/ai-workspace/blob/master/configs/config.example.yaml)
> 是**逐行權威參照**（每個 key 的完整註解都在那）。本頁不重抄註解，只給心智模型 + 導覽 + 情境對照。

---

## 1. 三種設定機制與載入順序

設定用**分層合併（layered merge）**：內建預設 → 你的 `config.yaml` 只寫 delta，其餘留在預設。

覆蓋手段，依偏好順序：

1. **改 `config.yaml`**：只寫你要改的那幾行，別的不用抄。
2. **`${ENV_VAR}` / `$ENV_VAR`**：字串值裡插環境變數，載入時代入。**密鑰一律用這個**（api_key、DSN…）。
   未設的變數 = 開機**大聲 raise**，不會靜默變空字串。
3. **`WORKSPACE_APP_CONFIG=/path/to/your.yaml`**：改讀哪個檔。

**config 檔的解析順序**（`python -m workspace_app`）：

```
--config / -c 旗標  >  $WORKSPACE_APP_CONFIG  >  ./config.yaml  >  內建預設（無檔也能跑）
```

> 範例檔放在 `configs/`，所以正式跑通常是 `uv run python -m workspace_app -c configs/config.yaml`
> （或設 `WORKSPACE_APP_CONFIG=configs/config.yaml`）。**注意預設只看 `./config.yaml`（當前目錄）**，
> 不是 `configs/config.yaml`——「改了沒生效」十之八九是讀到別的檔（開機 log 會印出實際讀的路徑）。

### 開機的嚴格驗證（typo / 錯接的防線）

開機會 fail-loud，別怕它凶——這些訊息幫你在部署當下就抓到錯：

- 未知 YAML key → raise（打錯字防線）
- preset 參照壞掉（`workspace_chat` / `kb_chat` / `infer_modules` / `kb.retrieval_llm` / `kb.wiki.llm`）→ raise
- `prompt_file` 指向不存在的檔 → raise
- 未設的 `${ENV_VAR}` → raise
- `agents.kb_chat` 解析後 `allowed_tools` 不含 `kb_search` → raise（KB 聊天接到 RCA preset 卻沒補工具的經典雷）
- `kb.retrieval_llm` 少了 `preset` → raise（要關就寫 `null`，別留半套）

### 開機還會吐兩份觀測資料（永遠開，無旋鈕）

- **resolved-config dump**：把「合併後的完整設定」印出來（標註每個值的來源、密鑰遮罩），
  同時寫一份真值到 `config.yaml` 旁的 `config.resolved.yaml`（`chmod 0600`）。跟 example 對 diff
  就知道哪些吃了預設。
- **LLM 呼叫記錄**：每次對外 litellm 呼叫留一筆可重播的完整記錄（見 [§12 觀測](#12-觀測-observability)）。

---

## 2. 最小起步（預設就能跑）

**完全不寫 `config.yaml` 也能起**——內建預設就是一套可跑組合：local sandbox、in-memory filestore、
Qwen3（透過 Ollama）、3 個 RCA picker、KB 聊天已接好 `kb_search`。

```bash
uv sync --all-extras
cd web && pnpm install && pnpm run build && cd ..
uv run python -m workspace_app            # API + SPA 一起跑在 127.0.0.1:8000
```

要 KB 檢索/嵌入真的動起來，需要本機有 Ollama（`bge-m3` 嵌入 + `qwen3` 生成）。要換模型或上正式環境，
才需要 `config.yaml`——往下看。

---

## 3. 情境速查表（我想做 X → 改哪個 key）

複雜的來源是「旋鈕多」，但**大部分部署只會動下面這幾格**。左欄是目標，右欄是要碰的 key（區塊詳解見後面）。

| 我想… | 改哪裡 |
|---|---|
| **全部換成 OpenAI / Claude** | `agents.presets.*.model` 改模型字串 + `agents.presets.*.llm.api_key: ${OPENAI_API_KEY}`（範例 2） |
| **只加一個調過 prompt 的模型到 picker** | 新增一個 `agents.presets.<name>` + 加進 `agents.workspace_chat[]`（範例 1） |
| **KB 聊天換模型** | 加 `agents.kb_chat[]` 條目；接非 `kb-default` 的 preset **必須**補 `allowed_tools: [kb_search]`（範例 3/4） |
| **選了 VLM 當主 agent，要牠自己直接看圖** | `agents.presets.<name>.vision: true`（[§7](#vlm-主-agent-直接讀圖vision)） |
| **檔案要持久化（重啟不掉）** | `filestore.kind: specstar` + `filestore.pg_dsn: ${SPECSTAR_PG_DSN}` + `disk_root` |
| **上多 pod（k8s）** | `sandbox.kind: http` + `sandbox.http.base_url` ＋ 共享 filestore ＋ 共享 MQ backend（見 [§5 階梯 C](#c-多-pod-k8s)） |
| **把 job runner 拆出 API** | `server.run_consumers: false`，另跑 worker pod（[§8 訊息佇列](#8-訊息佇列-message-queue)） |
| **設管理員（能讀所有 collection）** | `server.superusers: ["alice@example.com"]` |
| **限制上傳大小 / 每工作區配額** | `filestore.max_file_size` / `filestore.workspace_quota` |
| **換嵌入模型** | `kb.embedder.model` + 設 `KB_EMBED_DIM` 或 `KB_EMBED_MODEL`（**改維度＝要重建索引**） |
| **調 KB 檢索深度（recall vs 延遲）** | `kb.retrieval.enhancements`（`expand` / `hyde` / `rerank`） |
| **關掉 KB 的 multi-query/HyDE/rerank** | `kb.retrieval_llm: null` |
| **關掉 wiki 維護 / 圖片 VLM** | `kb.wiki.llm: null` / `kb.vlm_llm: null` |
| **模型會塞車 → 自動切備援** | preset 加 `fallbacks: [...]`；全域門檻在 `failover.*`（[§11](#11-忙碌時的-llm-備援-failover)） |
| **長時間 exec 不要被砍** | `sandbox.exec_timeout: 0` + 設 `sandbox.log_timeout`（idle 上限） |
| **關/搬 LLM 呼叫記錄** | 環境變數 `WORKSPACE_LLM_LOG=0` 或 `observability.llm_log.dir` |

---

## 4. 檔案長怎樣：區塊地圖

`configs/config.example.yaml` 的區塊（全是註解，取消註解才生效）：

| 區塊 | 是什麼 | 一般會不會動 |
|---|---|---|
| `server` | 監聽位址、`default_user`、`superusers`、`run_consumers`、cancel 輪詢 | 上線常改 superusers / run_consumers |
| `sandbox` | **agent 執行環境**（見 [§6](#6-sandbox-執行環境重點)） | 多 pod 一定改 |
| `tools` | RCA 工具包怎麼佈署（`prebuilt` / `uv-run`） | 開發時改 |
| `filestore` | 檔案儲存（`memory` / `specstar`）＋ 配額 / GC | 上線一定改 |
| `runner` | RCA agent loop 的 `max_retries` / `max_turns` | 少改 |
| `message_queue` | 背景 job 佇列後端（`simple` / `rabbitmq`） | 多 pod / 高吞吐才改 |
| `observability` | LLM 呼叫記錄 | 少改 |
| `failover` | 忙碌時的 LLM 備援全域門檻 | 有多模型才改 |
| `llm` | preset 沒寫 `llm.*` 時的預設 endpoint ＋ 抑制重複的取樣參數 | 少改 |
| `read_file` / `exec` | sandbox 工具的輸出上限 | 少改 |
| `history` | 跨回合記憶的訊息數 / token 預算 | 換大 context 模型時改 |
| `kb` | **KB 子系統**（見 [§9](#9-kb-子系統)） | 用 KB 就會改 |
| `agents` | **preset 庫 ＋ picker ＋ KB 聊天**（見 [§7](#7-agents心智模型與雷區)） | 幾乎一定改 |
| `health` | sanity matrix 的 AI 評審 | 診斷時才開 |

---

## 5. 三條部署階梯

從最簡單走到多 pod，每階只加必要的旋鈕。

### A. 本機開發（預設）

不用 `config.yaml`。`sandbox.kind: local`（tmpdir）、`filestore.kind: memory`、`message_queue.kind: simple`、
`server.run_consumers: true`（同進程消化所有 job）。

### B. 正式單機（單 pod）

要**持久化**與**管理員**：

```yaml
server:
  superusers: ["alice@example.com"]
filestore:
  kind: specstar
  pg_dsn: ${SPECSTAR_PG_DSN}
  disk_root: /data/specstar          # 檔案 blob 落地處
sandbox:
  kind: local
  root: /data/scratch                # sandbox 工作目錄（可與 disk_root 不同卷）
```

`run_consumers` 留 `true`：API 進程自己消化 index/wiki/card-gen/sanity 佇列（單機不用拆 worker）。

### C. 多 pod（k8s）

多個 app pod 要看到**同一份**檔案與 sandbox。三件事必須共享/協調：

```yaml
sandbox:
  kind: http                          # 正式後端：獨立的 sandbox-host 服務
  http:
    base_url: http://sandbox-host:8000
filestore:
  kind: specstar                      # 共享 Postgres + 共享 disk_root 卷
  pg_dsn: ${SPECSTAR_PG_DSN}
  disk_root: /data/specstar
message_queue:
  kind: simple                        # 騎在共享 specstar 後端上，多 pod 零額外基礎設施
server:
  run_consumers: false                # API 只當 producer；另跑 worker pod 消化 job
```

- **為什麼 `sandbox.kind: http`**：`http` 後端把「哪顆 sandbox 服務同一個 item」的地址存進共享 store
  並用 CAS 收斂，多 pod 才不會各開各的 sandbox 導致「檔案樹一下有一下沒」（#366）。
  ⚠️ 若你堅持用 `kind: local` 上多 pod，`sandbox.root` **必須指到共享 RWX 卷**——local 後端靠
  `{root}/{item_id}/root` 這個固定路徑讓每個 replica 解析到同一份活檔（#345）；指到本機路徑就會資料分裂。
- **worker pod**：`run_consumers: false` 後，各 JobType 各跑一個 worker（各自 k8s HPA 擴縮）：
  ```bash
  python -m workspace_app.worker index      # 也可 wiki / card-gen / sanity
  ```
  參考 `kubernetes/base/workers.yaml`。**前提是佇列後端要共享**（`simple` 騎共享 specstar，或 `rabbitmq`）。
- sandbox-host 是**獨立專案/映像**，用 `SANDBOX_HOST_*` 環境變數設定，不吃這份 config——見
  [§13 環境變數](#13-環境變數重點) 與 `deploy/sandbox-host.example.yaml`。

---

## 6. sandbox（執行環境）★重點

agent 要跑 shell 才**延遲**開 sandbox；純檔案操作走 FileStore，不會開。三種 `kind`：

| kind | 用途 |
|---|---|
| `local` | 子進程 + 暫存目錄。**本機/k8s 單卷共享**的預設。 |
| `http` | 把 sandbox 跑在獨立 host pod（`sandbox-host/`）。**正式多 pod 後端。** |
| `mock` | 記憶體用，測試用。 |

> `docker` 已**廢棄**（#252）→ 改用 `http`。

### 常動的旋鈕

```yaml
sandbox:
  kind: local
  root: null            # null = 每個 sandbox 一個 tmpdir。多 pod local：指到共享 RWX 卷（見 §5-C）
  exec_timeout: 60.0    # 單一指令的「總」牆鐘上限；0 = 不限
  log_timeout: 60.0     # 「閒置」上限（#70）：這麼久沒任何 stdout/stderr 就當卡死砍掉；0 = 關
  isolate: null         # null = 自動偵測 userns jail（與下面的 isolation 是兩回事）
  max_workspace_bytes: 0  # 單一 item 暫存目錄硬上限（bytes；0=不限）。防單一工作區塞爆共享卷
```

> **長任務**：把 `exec_timeout: 0`（不限總時間）＋設一個 `log_timeout`（只要還在吐 log 就不砍）。

### `kind: http`（正式後端）

```yaml
sandbox:
  kind: http
  http:
    base_url: http://sandbox-host:8000   # sandbox-host 的 ClusterIP Service
    read_timeout: 0                       # 0 = 不設 HTTP 讀取上限（由 host 端 timeout 收斂）
```

### per-item OS-user + cgroup 隔離（#345，選用，預設關）

當多個 item 目錄並排在同一共享卷上，可把 `LocalProcessSandbox` 換成 `IsolatedProcessSandbox`：每個 item 的
`exec` 用 `setpriv` 降到一個**穩定 uid**（`xxhash(item_id)`，每個 pod 都算出同一個），跑在 per-item cgroup v2 slice 下。

```yaml
sandbox:
  isolation:
    enabled: false      # true ⇒ 每次 exec 降到 per-item uid + cgroup；null = 自動（有 CAP_SETUID + 可寫 cgroup root 才開）
    uid_base: 1000000
    uid_range: 2000000000
    cgroup_root: null   # null = 自動偵測 pod 被委派的 cgroup v2 slice
    memory_max: 512M
    cpu_cores: 1.0
    pids_max: 512
```

- 預設關：多數叢集禁 `CAP_SETUID/SETGID` 與可寫 cgroup tree；[§5-C](#c-多-pod-k8s) 的共享目錄修法**不需要**它也能解資料遺失。
- 這**不是** `sandbox.isolate` 的 userns jail——是另一套模型（無 namespace）。pod 要帶 `CAP_SETUID/SETGID`
  ＋委派的 cgroup root（見 `kubernetes/base/deployment.yaml`）。
- `uv-run` 工具模式會強制關掉它。

### sandbox-host（獨立服務，不吃這份 config）

`kind: http` 連到的 host 是**自己的專案 `sandbox-host/`**（自己的依賴/映像），用 `SANDBOX_HOST_*`
環境變數設定（見 [§13](#13-環境變數重點)），不是這份 YAML。app 端只要 `sandbox.kind: http` + `http.base_url`。
細節見 `deploy/sandbox-host.example.yaml` 與 `docs/sandbox-host.md`。

---

## 7. `agents`：心智模型與雷區

這是最容易搞混、也最常改的區塊。心智模型：

- **`presets`**：LLM 後端的**庫**。每個 preset 綁一個 `model` + 系統 prompt + LLM endpoint（`base_url`+`api_key`），
  可選 `allowed_tools` / `suggestions` / sandbox image。
- **`workspace_chat[]`**：FE 的 RCA picker。**順序有意義——第一個是預設**，新調查自動掛它。每條參照一個 preset，可就地覆蓋任何欄位。
- **`kb_chat[]`**：KB 聊天面。形狀同 `workspace_chat`，但**解析後 `allowed_tools` 一定要含 `kb_search`**，否則開機 raise。
- **`infer_modules`**：模組推論分類器（#66），第一條帶 per-step 設定（`reasoning_effort` / `parallelism` / `collection`）。

`allowed_tools` 是**三態**：省略（None）= 給預設工具集；`[]` = 明確清空；`[a,b,c]` = 就這些。

### 三大雷區

1. **KB 聊天接非 `kb-default` 的 preset 卻沒補 `kb_search`** → 開機 raise（附修法路徑）。範例 3。
2. **picker 第一條就是預設**：想換預設模型，把它排第一。
3. **preset（哪顆模型）與 template 的 `_config.json`（哪些工具）正交合成**：picker 選 GPT，工具仍來自
   template（`rca-tools` / `ask_knowledge_base` 照給）。想改工具去改 profile，不是改 preset。

### VLM 主 agent 直接讀圖（`vision`）

主 agent 選了視覺模型（VLM）時，預設**牠仍看不到圖**：圖片一律繞去 `kb.vlm_llm` 那顆**獨立 VLM** 轉成文字再回來
（main → VLM → main）。兩次轉手＝慢，圖轉文＝掉資訊。在 preset 上標 `vision: true`，就告訴系統「這顆 model 自己看得到圖」：

```yaml
agents:
  presets:
    qwen-vl:
      model: ollama_chat/qwen3-vl
      vision: true          # ← 這顆 model 原生看得到圖
```

標了之後，兩種圖都直接進主模型的眼睛，**不再經過獨立 VLM、不再轉文字**：

| 圖從哪來 | 行為 |
|---|---|
| 使用者在對話框**夾/貼**的圖 | 送出當下就內嵌進該回合的使用者訊息 → 模型直接看到，**連工具都不用叫** |
| agent 自己在工作區**發現**的圖（`list_files` 找到、或自己產生的） | 牠呼叫 `read_image`，拿回的是**原圖**而非文字描述 |

雷區與邊界：

- **這是宣告式旗標，不自動偵測**：本地 Ollama 的 VLM model id 不在 litellm 的能力資料庫裡，自動偵測會誤判成「不支援」而默默失效——所以要你自己標。
- **預設 `false`＝維持原本行為**：純加法。沒標的 text-only 模型照舊走 `kb.vlm_llm` describer，一行行為都不變。
- **`read_image` 那條需要模型支援 tool calling**：有些 VLM（如 Ollama 上的 `qwen2.5vl`）根本不支援工具，那牠只吃得到「夾圖內嵌」這條路。
- **`kb.vlm_llm` 不能因此關掉**：它仍是 KB 攝取（圖片/PDF 視覺頁）與 text-only 主模型的 describer，兩者用途不同。

> `prompt_file` 三種寫法：`pkg:<dotted.package>/<file.md>`（隨 wheel 打包）、`/絕對路徑.md`、`相對路徑.md`（相對**這個 config 檔**的目錄）。
> 內建 8 個 preset 與完整範例 1–4，見 `config.example.yaml` 第 415 行起。

---

## 8. 訊息佇列（message queue）

兩條背景佇列——wiki 維護（#59）與 KB 索引（#82）——共用一個後端：

```yaml
message_queue:
  kind: simple          # simple | rabbitmq
```

- **`simple`**（預設）：job 就是共享後端上的 specstar resource，每個 pod 消化同一佇列。**多 pod 零額外基礎設施**
  （騎 specstar filestore 本來就需要的共享後端）。
- **`rabbitmq`**：broker 撐更高吞吐。旗鈕（`url` / `queue_prefix` / `max_retries` / `heartbeat_seconds`…）全可選，
  未設就吃 specstar 預設。⚠️ 慢的 index job 若比 `heartbeat_seconds` 久，要**調高**否則被回收。

搭配 `server.run_consumers`：
- `true`（預設）= 全包（本機/單 pod），API 進程自己消化。
- `false` = API 純 producer（仍註冊 + enqueue，只是不消化），另跑 worker pod 各消化一個 JobType（見 [§5-C](#c-多-pod-k8s)）。

---

## 9. KB 子系統

用到知識庫就會碰。重點分群：

```yaml
kb:
  embedder:
    model: ollama/bge-m3        # 換模型 = 重建索引事件；維度用 KB_EMBED_DIM / KB_EMBED_MODEL 對齊（見 §13）
    fallbacks: []               # 同一模型的副本 endpoint（不同嵌入模型會毀掉向量空間，別亂填）
  chunker: { max_tokens: 256, overlap: 32 }
  retrieval_llm:                # kb_search 的 multi-query / HyDE / rerank 用哪顆 LLM
    preset: kb-retrieval        # 寫 kb.retrieval_llm: null 可整組關掉
  retrieval:
    enhancements:               # recall ↔ 延遲的旋鈕，每個是 {default, max}
      expand: { default: 1, max: 3 }     # 改寫幾種問法；0=關
      hyde:   { default: 0, max: 1 }     # 假設文件探測；0=關
      rerank: { default: true, max: true }
    quality_weight: 0.10        # #105 文件品質先驗強度（很小是刻意的）；0=關
    quality_floor: null         # #105 絕對門檻：分數低於此的文件直接剔除；null=只降權不剔除
    sparse_corpus_cap: null     # 關鍵字（BM25）一次最多撈回幾個 chunk；null=不封頂（見下）
  max_searches_per_turn: 3      # 每則 KB 回覆的 kb_search 次數上限（#195）；null=不限
  max_searches_ceiling: 10      # FE per-message 次數 picker 的上限（#334）
  vlm_llm:   { preset: kb-vlm } # 圖片/PDF 視覺頁；null=圖片上傳存 0 chunk 直到設好再重索引
  wiki:
    llm: { preset: wiki-default }   # wiki 維護/閱讀 agent；null=關掉 wiki 維護
```

- **改嵌入維度 = 重建索引**：`DocChunk` 的向量欄寬在 class 定義時綁死，改了要重跑索引。
- `vlm_format_llm` / `deck_vlm` / `quality_judge` 省略時各自 fallback（`retrieval_llm` / `vlm_llm` / `retrieval_llm`）；
  細節與 off-switch 見 example 第 292 行起。

### `sparse_corpus_cap` —— 關鍵字檢索的封頂

檢索有**兩條各自獨立的路**：語意（向量）與關鍵字（BM25）。這個設定**只管關鍵字那條**。

關鍵字那條會先用 `text` 的三連字索引挑出「字面上像」的 chunk 再排序。問題是這個過濾**只對罕見詞
有效**：查 `quibblezorp` 可能只挑出 1 個，但查 `temperature` 這種常見詞，幾乎整個 collection
都「像」，等於沒縮 —— 而真實問句幾乎一定含常見詞。

`sparse_corpus_cap` 就是那條路的上限：**一次最多只從資料庫撈回這麼多個 chunk**（取最相似的），
不管有多少個命中。

| 設定 | 效果 |
| --- | --- |
| `null`（預設） | 不封頂。常見詞查詢可能把整個 collection 撈回來 |
| `1000` | 保守起步；幾乎不影響結果，但最壞情況大幅收斂 |
| `200` | 更快，但 BM25 看得到的範圍變窄，漏掉的機會上升 |

**為什麼封頂相對安全**：語意那條路**完全不受這個上限影響**，照樣搜遍每一個 chunk。所以某個 chunk
就算掉出關鍵字的前 N 名，語意搜尋仍可能找到它，兩邊結果最後會合併。剩下的風險很窄 —— 只有
「**只能靠字面完全比對才找得到**（例如料號、型號這種語意上沒特徵的字串）」**而且**又剛好掉出前 N 名，
兩個條件同時成立才會漏。

**調整前請先量**：用 #535 的檢索評測跑一次不封頂的 baseline，設了之後再跑一次，比 recall@k / MRR，
確認沒退步再往下調。評測需要真實 collection 與可用的 LLM。

> `quality_weight` / `quality_floor` 在此版之前**設了不會生效**（loader 接受該 key 但建構時被丟掉）。
> 現已修正 —— 若你的 `config.yaml` 早就寫了這兩個值，升級後它們會**開始真的作用**，
> `quality_floor` 尤其會開始剔除低分文件。

---

## 10. 其它常用小區塊

```yaml
runner:  { max_retries: 2, max_turns: 10 }                 # RCA agent loop
history: { max_messages: 40, max_context_tokens: 24000 }   # 跨回合記憶（預設吃本地 qwen3 ~32K；換大模型調高）
read_file: { max_lines: 2000, max_chars: 200000 }          # 讀檔工具上限
exec:    { output_max_chars: 30000, tool_output_max_chars: 200000 }
#          ↑ 單一指令輸出上限（比 read_file 小，因會跨回合累積）；同一預算也管
#            列表工具（list_files / list_sources）與寫入被拒時回吐的檔案內容。
#          tool_output_max_chars 是「任何一個工具單次結果」的絕對天花板，對每個
#            工具一律套用（不倚賴各工具自己記得節制）。它是保險絲，所以設在合理
#            單次答案的最寬處（一次完整 read_file）；小 context 模型請調低。
```

---

## 11. 忙碌時的 LLM 備援（failover）

有多顆模型、任一顆會塞車時，給 preset 一條 `fallbacks:`（其它 preset 名，依序），忙了就切下一顆並把忙的那顆
放 cooldown。全域門檻在 `failover.*`，preset 可逐一覆蓋（hosted 模型要放寬 `ttft_timeout_s`）：

```yaml
failover:
  ttft_timeout_s: 8       # streaming：這麼久沒吐第一個 token ⇒ 切
  cooldown_s: 30          # 忙的 (model,endpoint) 被跳過多久
  num_retries: 2          # 切之前同 endpoint 快速重試幾次
  round_backoff_s: [1, 2, 4, 8, 16]  # 整條掃完沒結果就重掃，長度=重掃輪數；[]=只掃一次
  total_deadline_s: 120   # 整回合上限；到了就吐可讀的「模型忙，稍後再試」而非卡死
```

互動用的 head preset 把重掃壓短（有人在等）；index/batch 的 head 反而要放長。範例見 example 第 235 行。

---

## 12. 觀測（observability）

```yaml
observability:
  llm_log:
    enabled: true      # 環境變數 WORKSPACE_LLM_LOG=0 可直接關（正式環境 off-switch）
    dir: logs/llm
    keep_days: 0        # 0 = 全留（手動清）
```

每筆生成呼叫留完整 JSON（`request` 區塊就是 `litellm.completion(**request)` 的 kwargs，可直接複製重播，
或跑落地的 `logs/llm/replay.py <file>`）；嵌入/rerank 只留一行索引。刪一天：`rm -rf logs/llm/<date>`。

---

## 13. 環境變數★重點

環境變數分**三類**，別搞混：

### A. 框架行為（程式直接讀）

| 變數 | 作用 |
|---|---|
| `WORKSPACE_APP_CONFIG` | 改讀哪個 `config.yaml`（優先於 `./config.yaml`，低於 `--config`） |
| `WORKSPACE_LLM_LOG` | `0` = 關掉 LLM 呼叫記錄（不用改 config 的正式 off-switch） |
| `WORKSPACE_AGENT_STREAM` | `0` = 非串流逃生門（agent 一次抓完整回應，不逐字串流） |
| `WORKSPACE_AGENT_DECIDE_THEN_ACT` | 開啟結構化 decide-then-act |
| `WORKSPACE_TOOLS_DIR` | 預建工具包目錄（等同 `tools` 區塊；`prebuilt` 模式的產物路徑） |
| `KB_EMBED_DIM` | **嵌入維度**（明確指定，優先最高）。設錯會毀掉向量欄 |
| `KB_EMBED_MODEL` | 沒設 `KB_EMBED_DIM` 時，用模型名去內建表推維度（單旋鈕設法） |
| `KB_CODE_EMBED_MODEL` | 同上，code 專用嵌入的維度來源 |

> **嵌入維度解析順序**：`KB_EMBED_DIM` 明確值 > `KB_EMBED_MODEL` 查內建表（bge-m3=1024、
> nomic-embed-text=768、openai text-embedding-3-small=1536…）> 都沒設且模型不認得 → **raise**
> （不靜默給預設，免得毀了 `DocChunk` 向量欄）。空/離線 → bge-m3 的 1024。

### B. `config.yaml` 裡的 `${...}` 密鑰（載入時代入，未設就 raise）

名字**由你在 config 裡自己取**，這些只是 example 用到的慣例名：

| 變數 | 出現在 |
|---|---|
| `SPECSTAR_PG_DSN` | `filestore.pg_dsn`（specstar Postgres DSN） |
| `RABBITMQ_URL` | `message_queue.rabbitmq.url` |
| `LLM_API_KEY` | 頂層 `llm.api_key`（preset 沒寫時的預設 endpoint） |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | `agents.presets.*.llm.api_key` |
| `KB_EMBED_API_KEY` / `KB_CODE_EMBED_API_KEY` | `kb.embedder.api_key` / `kb.code_embedder.api_key` |
| `GIT_DEFAULT_TOKEN` | `kb.git.default_token`（自架 GitLab 的 PAT） |
| `RUN_CONSUMERS` | `server.run_consumers`（可用 env 控制 producer/consumer 角色） |

### C. sandbox-host 服務（**獨立進程/映像，不吃這份 config**）

只有你用 `sandbox.kind: http` 時、設在 **sandbox-host pod** 上，見 `deploy/sandbox-host.example.yaml`：

`SANDBOX_HOST_BIND`、`SANDBOX_HOST_UID_MIN` / `_UID_MAX`、`SANDBOX_HOST_MEMORY_MAX`、
`SANDBOX_HOST_CPU_CORES`、`SANDBOX_HOST_PIDS_MAX`、`SANDBOX_HOST_CGROUP_ROOT`、`SANDBOX_HOST_ROOT`、
`SANDBOX_HOST_EXEC_TIMEOUT`、`SANDBOX_HOST_LOG_TIMEOUT`、`SANDBOX_HOST_TOOLS_DIR`、`SANDBOX_HOST_IDLE_TTL`
（＋ host 綁定用的 `POD_IP` / `PORT`）。

---

## 14. 常見「改了沒生效 / 開機報錯」對照

| 症狀 | 多半是 |
|---|---|
| 改了設定沒反應 | 讀到別的 config 檔——看開機 log 印的 `config: <path>`，確認不是預設的 `./config.yaml` |
| 開機 `unknown key` | YAML key 打錯字（嚴格驗證） |
| 開機抱怨 `kb_search` | `kb_chat` 接了沒 `kb_search` 的 preset（補 `allowed_tools: [kb_search]`） |
| 開機 `${X}` raise | 密鑰環境變數沒設 |
| 多 pod 檔案樹一下有一下沒 | 用了 `kind: local` 卻沒共享 `sandbox.root`，或該用 `kind: http`（見 §5-C） |
| KB 搜不到東西 / 維度錯 | 換了嵌入模型沒對齊 `KB_EMBED_DIM` 且沒重建索引 |
| 圖片上傳 0 chunk | `kb.vlm_llm` 沒設 / 被設成 `null` |

---

## 相關文件

- [`configs/config.example.yaml`](https://github.com/HYChou0515/ai-workspace/blob/master/configs/config.example.yaml) — 逐行權威參照（每個 key 的完整註解）
- [部署指南 deployment.md](deployment.md) — 用程式/factory 換整塊實作、生產環境注意事項
- [開發者導覽 index.md](index.md) — 30 秒心智模型與抽換點
- [系統架構 architecture.md](architecture.md) — 每層職責與為什麼這樣切
- `deploy/sandbox-host.example.yaml` — sandbox-host 服務的 `SANDBOX_HOST_*` 設定
- `kubernetes/base/` — 多 pod 佈署範本（`deployment.yaml` / `workers.yaml` / `pvc.yaml`）
