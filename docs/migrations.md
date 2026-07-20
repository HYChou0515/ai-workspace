# 資料遷移（Migrations / 索引回填）

有些升版會改變「資料在資料庫裡的儲存形狀」，但 **specstar 只在寫入當下**把一列的
`indexed_data` 算好，之後**不會自動回填**。所以既有的資料列會停在舊形狀，直到每一列
被**重新寫過一次**為止。本文說明什麼時候需要做這件事、怎麼用內建腳本做、以及做完之後
怎麼把索引空間收回來。

> 一句話：**升版只讓「未來的寫入」變乾淨；既有的列要靠 migrate 重寫才會跟上。**

---

## 1. 什麼時候需要跑

當一次部署做了下面任一件事，既有列就會落後，需要一次 migrate：

- **新增了一個索引**到既有 model（例如替某欄位加上 `IndexableField`）。舊列在那個
  索引加入前就寫好了，不會出現在新索引裡，聚合時會少算。
- **改變了 `indexed_data` 的算法**。最典型的就是 **specstar 0.12.1**：`Vector` 欄位
  不再被複製進 `indexed_data`（它本來就有自己的 pgvector 欄位），但這只對**新寫入**
  生效；既有列的 `indexed_data` 還帶著整條 4096 維向量，被 GIN 逐元素索引 —— 那正是
  讓文件列表變慢的元凶（見 §5）。

如果你這次部署沒動索引、也沒動 `indexed_data` 的算法，就**不需要**跑 migrate。

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
裡。針對 §5 的向量清理，這個程式碼變更**已經做好並隨版本發出**。

`_reindex_only` 的定義與用法就在同一個檔案；每一個既有的 `Schema(...).step(...)` 都是
同一個模式。要為未來的清理新增一步，照抄即可。

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

## 5. 目前的具體案例：specstar 0.12.1 向量清理

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

## 6. 目前的具體案例：`doc-chunk` 的 `text` 索引回填（關鍵字檢索）

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

## 7. 注意事項

- **挑低流量時段**：migrate 會重寫每一列的 meta。
- **順序**：先 dry-run，再正式跑，最後 `REINDEX`。
- **可重複執行**：`_reindex_only` 是 identity；重跑一個已經在最新版的 model 只會得到
  一整排 `skipped`，不會壞事。
- **未來要新增一次清理**：在 `resources/__init__.py` 幫該 model 的 `Schema` 加一個
  `_reindex_only` step 升版，然後把 model 名字丟給這支腳本即可 —— 機制是通用的，這支
  腳本不綁定任何特定 model。
