# 資料遷移（Migrations / 索引回填）

有些升版會改變「資料在資料庫裡的儲存形狀」，但 **specstar 只在寫入當下**把一列的
`indexed_data` 算好，之後**不會自動回填**。所以既有的資料列會停在舊形狀，直到每一列
被**重新寫過一次**為止。本文說明什麼時候需要做這件事、怎麼用內建腳本做、以及做完之後
怎麼把索引空間收回來。

> 一句話：**升版只讓「未來的寫入」變乾淨；既有的列要靠 migrate 重寫才會跟上。**

**趕時間的話直接看下面的 §5 案例總表** —— 目前有哪些 model 需要回填、沒跑會怎樣，都在
那一張表裡。其中 **`workspace-file`（§8）會讓 rollout 停住**，而且回填**不能打 Service**。

---

## 1. 什麼時候需要跑

當一次部署做了下面任一件事，既有列就會落後，需要一次 migrate：

- **新增了一個索引**到既有 model（例如替某欄位加上 `IndexableField`）。舊列在那個
  索引加入前就寫好了，不會出現在新索引裡，聚合時會少算。
- **改變了 `indexed_data` 的算法**。最典型的就是 **specstar 0.12.1**：`Vector` 欄位
  不再被複製進 `indexed_data`（它本來就有自己的 pgvector 欄位），但這只對**新寫入**
  生效；既有列的 `indexed_data` 還帶著整條 4096 維向量，被 GIN 逐元素索引 —— 那正是
  讓文件列表變慢的元凶（見 §6）。
- **改變了「衍生欄位」的算法**。有些欄位不是原始輸入，而是從原始欄位算出來的比對鍵
  （例如 graph 的 `norm_subject` / `norm_period`）。規則一改，既有列還帶著舊規則算出來的
  鍵。這一種要跑的不是 no-op 重抽，而是一個**真的會改資料**的 step（見 §2、§9）。

如果你這次部署沒動索引、沒動 `indexed_data` 的算法，也沒動任何衍生欄位的規則，就
**不需要**跑 migrate。

---

## 2. 為什麼「光升版」不夠 —— 兩層機制

migrate 是「把一列重跑一次目前的寫入路徑」，重跑時 `indexed_data` 會被**重新萃取**成
最新形狀。但它有一個保護：

> **migrate 會跳過任何「已經在最新 schema 版本」的列。**（specstar 在 route 層與
> `ResourceManager.migrate` 各有一道 gate。）

也就是說，如果一個 model 的既有列都已經在最新版本，`POST /{model}/migrate/execute`
會對每一列回報 `skipped`，什麼都不做。要讓 migrate 真的動手，必須先給那個 model 的
`Schema` **升一版**，用一個 **no-op 的 `_reindex_only` step**（資料不變，只是逼出「重新
萃取 + 重新寫回」這個副作用）——這是**程式碼變更**，在 `src/workspace_app/resources/__init__.py`
裡。針對 §6 的向量清理，這個程式碼變更**已經做好並隨版本發出**。

**step 有兩種形狀，別只記得第一種：**

| 形狀 | step 函式 | 資料 | 例子 |
| --- | --- | --- | --- |
| **重抽索引** | `_reindex_only`（identity） | 不變 | §6 §7 §8 —— 目的純粹是逼出「重新萃取 + 寫回」這個副作用 |
| **重算衍生欄位** | 真的回傳一個改過的 record | **會變** | §9 的 `graph-claim`：`_renormalize_claim` 依當前規則重算比對鍵 |

兩種都必須是**純函式**（只讀該列自己的欄位，不能載入別的 resource），寫法照抄既有的即可。
差別在於第二種**改得動資料**，所以 dry-run（§3）在它身上更值得跑一次。

> ⚠️ **不是每個 `Schema` 都住在 `resources/__init__.py`。** `workspace-file` 的在
> `src/workspace_app/filestore/specstar_impl.py`，各個 job model 的在自己的 coordinator
> 裡。要盤點「還有誰需要 migrate」，請對整個 `src/` grep `Schema(` —— 只看
> `resources/__init__.py` 會漏掉 §8，而 §8 正好是唯一一個會卡住部署的。

---

## 3. 怎麼跑 —— `scripts/run_migrate.py`

腳本會對每個 model 打它的 migrate route、串流進度、依狀態統計
（`skipped` / `success` / `failed`），最後印出回收空間要下的 SQL。

**先 dry-run**（走 `migrate/test`，串流一模一樣的進度但**不寫回**）：

```bash
uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member
```

確認沒有 `failed` 之後，**正式跑**（會重寫每一列的 meta，請挑低流量時段）：

```bash
uv run python scripts/run_migrate.py doc-chunk cluster-member
```

非預設主機、或有掛 `root_path`：

```bash
uv run python scripts/run_migrate.py --base-url https://kb.example.com doc-chunk
```

route 掛在 `/api` 底下（`POST /api/{model}/migrate/execute`），身分沿用部署設定的
`server.default_user`，所以不需要另外帶 token。任何一個 model 出現 `failed` 或連線
失敗，腳本會以 **exit code 1** 結束並列出是哪些列。

---

## 4. 收尾：`REINDEX` 回收空間

migrate 把每一列重寫成精簡的 `indexed_data` 之後，**查詢速度會立刻恢復**（GIN 不再需要
比對那條向量），但**索引檔本身的體積**要等 `REINDEX` 才會縮回來 —— 舊的索引項會留成
dead entry。腳本會在成功後把要下的指令印出來，形如：

```sql
REINDEX TABLE CONCURRENTLY doc_chunk_meta;
REINDEX TABLE CONCURRENTLY cluster_member_meta;
```

- 用 `REINDEX TABLE`（而非指名某個索引），因為它按 **table 名**運作、對 specstar 的
  索引命名細節免疫；一次把該 meta table 的所有索引都重建乾淨。
- meta table 的名字是 `<table_prefix><model 的 snake 形>_meta`。預設部署沒有前綴，
  所以 `doc-chunk` → `doc_chunk_meta`、`cluster-member` → `cluster_member_meta`。若你的
  部署有設 `table_prefix`，用 `--table-prefix` 讓腳本把它印進去。
- `CONCURRENTLY` 不鎖表，可以在服務運作中一個一個跑。

---

## 5. 案例總表

每一列都是一次「既有資料會落後」的升版。**這張表是 repo 的事實**（哪個版本、哪個 commit
帶進來的）；**某個環境跑過沒有，repo 看不到**，要各環境自己確認。

| model | 現行 Schema | 帶進來的 commit | 沒回填的話 | 細節 |
| --- | --- | --- | --- | --- |
| `workspace-file` | v3 | `01b42392`（2026-07-29） | ⚠️ **新 pod 永遠不 ready、rollout 停住** | §8 |
| `doc-chunk` | v6 | `38b4ab58`（2026-07-20） | 文件列表慢；舊 chunk 的關鍵字檢索找不到 | §6 §7 |
| `cluster-member` | v1 | `cfe021e8`（2026-07-16） | 文件列表慢 | §6 |
| `graph-claim` | v3 | `19aa23b3`（2026-07-24） | 比對鍵停在舊規則 | §9 |
| `graph-mention` | v2 | `c1616a0d`（2026-08-03） | 比對鍵停在舊規則；走訪也要逐列解 blob | §9 |
| `graph-entity` | v1 | `e21369fb`（2026-08-03） | 走訪要逐列解 blob，慢 | §9 |
| `graph-entity-link` | v1 | `e21369fb`（2026-08-03） | 走訪要逐列解 blob，慢 | §9 |
| `graph-relationship` | v1 | `e21369fb`（2026-08-03） | 走訪要逐列解 blob，慢 | §9 |

一次盤點全部（**dry-run 不寫回，安全**，§3）：

```bash
uv run python scripts/run_migrate.py --dry-run \
  workspace-file doc-chunk cluster-member \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
```

已經在最新版的 model 會回一整排 `skipped`；沒有任何 collection 開 `use_graph` 的部署，那五
張 graph 表根本是空的 —— 兩種都不會壞事（§10），所以整串一起 dry-run 是安全的盤點方式。

⚠️ **正式跑的時候 `workspace-file` 要單獨處理**：它對「請求打到哪一個 pod」有硬要求，跟著
上面那串一起打會**回報成功、實際上什麼都沒做**。見 §8。

---

## 6. 案例：specstar 0.12.1 向量清理

0.12.1 讓 `Vector` 欄位不再進 `indexed_data`。**受影響的是兩個帶向量的 model**：

| model | 向量欄位 | schema 動作 |
| --- | --- | --- |
| `doc-chunk` | `embedding` / `embedding_alt` / `embedding_img` | v4 → **v5** 加一個 `_reindex_only` step |
| `cluster-member` | `embedding` | 原本**完全沒有 `Schema`**（migrate 會直接報錯），本次補上 `Schema` + `None → v1` step |

完整流程：

```bash
# 1. 部署帶有 0.12.1 + 上述 schema bump 的版本
# 2. dry-run 確認
uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member
# 3. 正式重寫
uv run python scripts/run_migrate.py doc-chunk cluster-member
# 4. 回收空間（腳本會印出這兩行）
#    psql:  REINDEX TABLE CONCURRENTLY doc_chunk_meta;
#           REINDEX TABLE CONCURRENTLY cluster_member_meta;
```

**範圍請留意**：這次清的是 **`indexed_data` 這個 JSONB 欄位與它的 GIN** —— 這是讓查詢
變快的關鍵。每一列的完整 meta（含向量）在另一個 `data` BYTEA 欄位裡還有一份，這次
**刻意不動**（當初拍板的「第一層」決定）。所以這修好的是**查詢速度**，不是完整的磁碟
體積；要連 BYTEA 一起收是另一個更大的決定（會改變讀取端反序列化到的內容），不在此列。

---

## 7. 案例：`doc-chunk` 的 `text` 索引回填（關鍵字檢索）

檢索改成**不再整包載入整個 collection** 之後，關鍵字（BM25）那半段改用 `DocChunk.text`
上的 **pg_trgm 索引**先把候選集縮小。而索引要看得到一列，`text` 必須先被萃取進那一列的
`indexed_data` —— 萃取**只發生在寫入當下**（就是 §2 那條規則）。

**所以升版後、回填前，既有的 chunk 對關鍵字檢索是隱形的：**

| | 回填前 | 回填後 |
| --- | --- | --- |
| 既有 chunk 的**關鍵字**檢索 | ❌ 找不到 | ✅ 正常 |
| 既有 chunk 的**語意（向量）**檢索 | ✅ 不受影響 | ✅ 正常 |
| **新上傳**的檔案 | ✅ 立即正常 | ✅ 正常 |

語意檢索照常運作，所以症狀不是「整個搜不到」，而是**舊文件的關鍵字命中率掉下去**——
這種半殘狀態不會噴錯，只會安靜地少給答案，所以請把回填當成部署的一部分，不要拖。

schema 動作：`doc-chunk` v5 → **v6**，一樣是一個 `_reindex_only` step（資料不變，只是逼出
「重新萃取 + 寫回」的副作用）。指令與 §3 相同：

```bash
# 1. 部署帶有 v6 的版本
# 2. 先 dry-run，確認沒有 failed
uv run python scripts/run_migrate.py --dry-run doc-chunk
# 3. 正式重寫（會重寫每一列的 meta，挑低流量時段）
uv run python scripts/run_migrate.py doc-chunk
```

**不需要手動建索引**：pg_trgm 擴充與那個 GIN 都由 specstar 在**每次開機**時確保存在
（`CREATE EXTENSION IF NOT EXISTS pg_trgm` + 建索引），只要 DB role 有權限即可。

**代價**：`text` 進了 `indexed_data`，等於每個 chunk 的文字在 JSONB 裡**多存一份**。這是
「用索引換掉整包載入」刻意付的成本，不是意外。大量重寫之後可以照 §4 跑一次
`REINDEX TABLE CONCURRENTLY doc_chunk_meta;`——這裡不是為了回收空間（`indexed_data` 是
變大的），而是讓重寫後的索引更緊實。

---

## 8. 案例：`workspace-file` 的 `path` 索引回填 —— 這一個會**卡住 rollout**

檔案樹和各種 entity 列表都走 `ls(prefix=…)`，而它把查詢**下推**到 `WorkspaceFile.path`
這個索引上（不下推的話，一個 3000 檔的 workspace 光列一次就要 796ms，而一次互動會呼叫
大約十次）。索引要看得見一列，`path` 必須先進那一列的 `indexed_data` —— 又是 §2 那條規則。

**這一個的後果和前兩個案例不同。** §6 是變慢、§7 是關鍵字命中率下降，都還能服務；`path`
沒回填的話，`ls(prefix=…)` 對舊列**一列都不回**，使用者看到的是**空的檔案樹、空的 entity
列表**，而資料完好無損。三個 replica 滾動更新時新舊 pod 答案不一樣，畫面會**閃爍** ——
讀起來就是資料遺失。

所以這一個帶了一道閘門：**`/api/readyz` 在回填完成前一律回 503**（`prefix_index_ready()`，
在 `src/workspace_app/api/app.py`），而 k8s 的 readinessProbe 就指著它
（`kubernetes/base/deployment.yaml`）。實務上的意思是：

> **忘記跑，你不會看到「慢」或「怪怪的」——你會看到新 pod 永遠不 ready、rollout 停在那裡。**

這是刻意的：寧可卡住，也不要讓任何一個 pod 端出「看起來是空的」workspace。liveness 故意
**沒有**指著同一個檢查，否則等待回填的 pod 會變成 crashloop —— 而 operator 正需要那些 pod
來跑回填。

### ⚠️ 回填必須打在**新 pod** 上，不能打 Service

這是這一節最容易做錯、而且**做錯了看起來像成功**的一步。

Service 只把流量導給 **ready** 的 pod，而卡住的時候 ready 的全都是**舊 pod**。舊 pod 的程式
裡 `WorkspaceFile` 的最新版是 **v2**（`indexed_fields` 沒有 `path`），而 migrate 只會把每一列
帶到「**該 pod 認得的**最新版」。所以打在 Service 上會**回報一整排成功、卻沒有把 `path` 抽
出來**，新 pod 依然永遠不 ready。

新 pod 雖然不 ready，但它**還活著**（liveness 走 `/openapi.json`），所以繞過 Service 直接連它：

```bash
# 1. 找一個新的、還沒 ready 的 pod
kubectl get pods -l app=rca-app

# 2. 直接連那個 pod —— port-forward 不經過 Service，不 ready 也連得到
kubectl port-forward pod/<新 pod 名稱> 8000:8000
```

### 部署順序

1. **先 rollout**。新 pod 起來但不會 ready —— 這是預期的；舊 pod 繼續服務，**沒有中斷**。
2. **port-forward 到一個新 pod**（上一小節），然後對著它跑：

   ```bash
   uv run python scripts/run_migrate.py --dry-run --base-url http://localhost:8000 workspace-file
   uv run python scripts/run_migrate.py           --base-url http://localhost:8000 workspace-file
   ```

3. **新 pod 自己會變 ready**，rollout 自然走完。不需要重啟任何東西。

要確認到底生效了沒，直接問那個 pod：

```bash
curl -i http://localhost:8000/api/readyz
# 503 + "workspace-file path index not backfilled" → 還沒好
# 200 + "ok"                                       → 好了
```

**全新安裝不受影響**：沒有舊列的時候，「`path` 以 `/` 開頭的列數」等於總列數，`readyz`
一開始就是綠的。

`path` 進 `indexed_data` 會讓索引變大（和 §7 同一種刻意付的代價）。大量重寫之後可以照 §4
跑 `REINDEX TABLE CONCURRENTLY workspace_file_meta;` —— 目的是讓索引緊實，不是回收空間。

---

## 9. 案例：graph 的五個 model（#534）

knowledge graph 的五個 model 各有一次升版，都需要一次回填：

| model | Schema | step 的形狀 | 沒回填的話 |
| --- | --- | --- | --- |
| `graph-claim` | v2 → **v3** | **重算衍生欄位**（`_renormalize_claim`） | 比對鍵停在舊規則 |
| `graph-mention` | → **v2** | **兩種都有**：`None → v1` 重算衍生欄位、`v1 → v2` 重抽索引 | 比對鍵停在舊規則；走訪也要逐列解 blob |
| `graph-entity` | 無 → **v1** | 重抽索引 | 走訪要逐列解 blob，慢 |
| `graph-entity-link` | 無 → **v1** | 重抽索引 | 走訪要逐列解 blob，慢 |
| `graph-relationship` | 無 → **v1** | 重抽索引 | 走訪要逐列解 blob，慢 |

```bash
uv run python scripts/run_migrate.py --dry-run \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
uv run python scripts/run_migrate.py \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
```

**嚴重度和 §8 差一級，不要混為一談。** `graph-entity` / `graph-entity-link` /
`graph-relationship` 這三個純粹是重抽索引，而且讀取端對「還沒回填」的列會**退回去讀
blob**，不會把缺席的欄位讀成空值 —— 所以這三個的回填是**加速**（走訪整個語料時只掃
metadata、不必逐列反序列化），**不是正確性閘門**。

**`graph-claim` 和 `graph-mention` 這兩個不一樣**，它們帶的是會改資料的 step：
`_renormalize_claim` / `_renormalize_mention` 依**當前**規則重算比對鍵
（claim 是 `norm_subject` / `norm_attribute` / `norm_period` / `norm_unit`，
mention 是 `norm_surface` / `norm_kind`）。沒回填的列還帶著舊規則算出來的鍵，依現行規則
本該視為同一件事的兩列，可能還是兩件。每一列都記著產生它的 schema 版本，所以「哪些還停在
舊規則上」是查得出來的，不是猜的。


**範圍**：graph 是 per-collection opt-in（`Collection.use_graph`，預設 `False`）。沒有任何
collection 開過的部署，這五張表是空的，跑起來不會有任何列 —— 跑一次當作確認即可。

---

## 10. 注意事項

- **挑低流量時段**：migrate 會重寫每一列的 meta。
- **順序**：先 dry-run，再正式跑，最後 `REINDEX`。
- **可重複執行**：重跑一個已經在最新版的 model 只會得到一整排 `skipped`，不會壞事。
  「重抽索引」型的 step 是 identity，所以這件事是白送的；**「重算衍生欄位」型不是
  identity**（§9 的 `graph-claim`），它可以重跑是因為每次都從該列的原始欄位重算 ——
  新增這種 step 的時候要**自己確認**這一點，別當成理所當然。
- **盤點要對整個 `src/` grep `Schema(`**，不是只看 `resources/__init__.py`（§2 的警告）。
- **未來要新增一次清理**：幫該 model 的 `Schema` 加一個 step 升版，然後把 model 名字丟給
  這支腳本即可 —— 機制是通用的，這支腳本不綁定任何特定 model。**順手把它加進 §5 的
  總表**，否則下一個人不會知道要跑它 —— §8 和 §9 就是這樣漏掉的：文件停在 2026-07-20，
  而那之後又有兩批升版沒人補上來。
