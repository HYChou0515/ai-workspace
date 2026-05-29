# RCA 3.0 — Plan: P3 程式碼 KB(git clone + code-specialized embedder)

> 跟 `plan-llamaindex-ingest.md` 的 P3 簡述對齊;這份是 full plan。經 `/grill-me`
> 走完決策樹後定案。可勾選追蹤文件。
>
> 規格細節進 contract.md / architecture.md;這裡記要做什麼、為什麼、順序、進度。

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P3.0** | Backend:Collection 加 git 欄位、第二個 embedder slot、DocChunk 加 `embedding_alt`、CodeSplitter dispatch (py/ts/tsx/js/jsx)、git clone ingest、parallel retrieval (group-by-embedder + RRF merge)、手動 sync endpoint、**背景 sync sweeper(已內含)** | ✅ |
| **P3.1** | cross-file reference (softlink prepend)、FE「New code collection」表單 + 「Sync now」按鈕 | ⏸ |
| **P3.2**(未來) | LSP 級 cross-file references、code-specific Reranker、`.go`/`.rs`/`.java`/... 語言擴充 | ⏸ |

每階段完成定義:`uv run ruff check && ruff format --check && ty check` 全清、後端
`coverage report` **100%**、commit 完成、本表打勾。

---

## 1 · 範圍 + 決策表

### Grill-me 決策

| 問 | 答 | 為什麼 |
|---|---|---|
| 程式碼怎麼進來? | **B git clone**(自架 gitlab) | 可追溯 SHA/branch + 可重 sync;upload 沒法 traceback |
| 資料模型? | **(i) Collection 加 optional git 欄位** | 不分裂 model;沒填 git_url = 普通 doc collection |
| 認證? | **(b) PAT** 存 `Collection.git_token` 明文 | 部署假設 KB DB 在受控環境(自架內網)。SaaS 場景另行升級到加密 |
| Clone 位置? | **(a) ephemeral** — clone → walk → ingest → 刪 tmpdir | 不留 working copy。大 repo 慢點接受;省 disk 管理 |
| Sync 觸發? | **(b) 手動 + 排程** | 手動 = 立刻;排程 = 例行更新。webhook v2 再說 |
| Splitter 語言? | **A tree-sitter-language-pack**(50MB),starter set `.py/.ts/.tsx/.js/.jsx`,其他走 SentenceSplitter | 一個 wheel 包所有 grammar |
| Embedder? | **(d) per-collection embedder + 兩路平行 retrieval** | bge-m3 對 code 中等;code-specialized model 顯著好;不能跨 vector space 比較,所以分路查 + RRF 合 |
| 跨檔 reference? | **v1 不做**(P3.1 加 softlink prepend) | 純加功能,不擋路 |
| FE? | **v1 不做** | 另一個 PR;backend ready 就好 |

### 什麼不變

- **API**:`/kb/*` 既有 endpoints 完全不動。**新增**:`POST /kb/collections/:id/sync`、`POST /kb/collections` 接 git 參數。
- **Storage layer**:specstar `SourceDoc`/`Collection`/`DocChunk` 仍是底層。`DocChunk` 加一欄 `embedding_alt`(optional)。
- **Retriever 核心邏輯**(BM25/RRF/MMR/parent-doc merge):不換。加一層 group-by-embedder。
- **`kb/links.py`**(markdown cross-doc rewriting):無關 code,不動。

### 什麼新增

- `Collection.git_url`/`git_branch`/`git_token`/`git_last_sha`/`git_last_pulled_at`/`embedder_id`/`sync_interval_hours`(都 optional;沒填 = 普通 doc collection)
- `DocChunk.embedding_alt: list[float] | None`(dim 由 alt embedder 決定)
- `kb/code_repo.py`(新)— git clone + file walk
- `kb/li_pipeline.py` 擴 `DispatchSplitter` 加 CodeSplitter dispatch
- Settings:`KB_CODE_EMBED_MODEL` / `KB_CODE_EMBED_DIM` / `KB_CODE_EMBED_QUERY_PREFIX` / `KB_CODE_EMBED_DOC_PREFIX`
- `factories.get_code_embedder(settings)`
- `Retriever.search` 內加 group-by-embedder + per-group dense + 全局 BM25(共用 chunk text) → RRF
- `POST /kb/collections/:id/sync` endpoint(同步觸發)
- `POST /kb/collections` 接 `git_url`/`git_branch`/`git_token`/`embedder_id` 等新欄位

---

## 2 · P3.0 — Backend infrastructure

### 2.1 Schema 變更

**`Collection` 加欄位**(都 optional/default,既有 collection migration 無影響):

```python
class Collection(Struct):  # → resource "collection"
    name: str
    description: str = ""
    icon: str = "layers"
    # ↓ P3 新增
    git_url: str | None = None              # https://gitlab.internal/...
    git_branch: str = "main"
    git_token: str | None = None            # PAT,used as basic-auth password
    git_last_sha: str | None = None         # 上次 sync 到的 commit
    git_last_pulled_at: int | None = None   # epoch ms
    embedder_id: int = 0                    # 0 = default embedder, 1 = alt (code)
    sync_interval_hours: int | None = None  # None = 只手動 sync
```

驗證:`embedder_id ∈ {0, 1}`;0 對應 `Settings.kb_embed_model`、1 對應
`Settings.kb_code_embed_model`。Server 啟動時若 `embedder_id=1` 但 alt embedder
沒 configure,警告但不擋。

**`DocChunk` 加 `embedding_alt`**:

```python
class DocChunk(Struct):
    # ... existing ...
    embedding: Annotated[list[float], Vector(dim=EMBED_DIM, distance="cosine")]
    embedding_alt: Annotated[
        list[float], Vector(dim=CODE_EMBED_DIM, distance="cosine")
    ] | None = None  # populated for `embedder_id=1` collections; None otherwise
```

驗證:`embedding_alt` 跟 `embedding` 至少一個非空;ingest 時根據 collection 的
`embedder_id` 寫對欄位。Retriever 查時根據 collection's embedder_id 挑欄位。

### 2.2 Settings 新增

```python
@dataclass
class Settings:
    # ... existing ...
    # P3: code-specialized embedder (the "alt" embedder)
    kb_code_embed_model: str = ""          # "" → no code embedder available
    kb_code_embed_dim: int = 768            # nomic-embed-code 預設
    kb_code_embed_query_prefix: str = ""
    kb_code_embed_doc_prefix: str = ""
    kb_code_embed_timeout: float = 60.0
    kb_code_embed_num_retries: int = 2
    kb_code_embed_batch_size: int = 64
    # 跟 chat LLM 共用 base_url/api_key,或獨立?v1 共用既有的 kb_embed_*
    # 若需要不同端點,P3.1 加 kb_code_embed_base_url

    # P3: git clone
    git_clone_dir: str = "/tmp/rca-clones"      # ephemeral working dir
    git_ca_cert_path: str | None = None          # 自架 gitlab 內部 CA
    git_clone_timeout_sec: int = 300             # 5 min upper bound

    # P3: scheduled sync
    sync_check_interval_sec: int = 300           # background task 多久檢查一次
```

### 2.3 Git clone subsystem(`kb/code_repo.py`,新檔)

```python
class CodeRepoIngestor:
    """Clone a git repo to a tmpdir, walk source files, hand them to the
    Ingestor as if uploaded individually. Ephemeral — tmpdir deleted after."""

    def __init__(self, *, clone_dir: Path, ca_cert: Path | None, timeout_sec: int): ...

    def clone_and_walk(
        self, *, url: str, branch: str, token: str | None
    ) -> Iterator[tuple[str, bytes]]:
        """Yields (relative_path, file_bytes) for each code-mime member of the
        repo. Filters by extension (only .py/.ts/.tsx/.js/.jsx for v1; others
        skipped). Returns the last commit SHA via a side channel (last_sha
        attribute or yields a sentinel) so the caller persists it."""
        # subprocess: git clone --depth=1 --branch=<b> <url> <tmpdir>
        # walk tmpdir recursively, filter by extension
        # rev-parse HEAD to get SHA
        # rm -rf tmpdir in finally
```

Auth pattern:`https://x-access-token:<TOKEN>@gitlab.internal/path/repo.git`。
CA:`GIT_SSL_CAINFO=<path>` env。Tests:mock `subprocess.run`(別真的 clone)。

### 2.4 Multi-embedder ingest

`Ingestor.__init__` 多收一個 `alt_embedder: Embedder | None`。
`_index_via_pipeline`:

- 看 SourceDoc 屬於的 collection 的 `embedder_id`,決定走 default pipeline 還是
  alt pipeline。
- 寫 DocChunk 時根據 `embedder_id` 把 vector 寫到 `embedding` 或 `embedding_alt`。

`build_doc_pipeline(*, embedder, ...)` 不變;新增 `build_code_pipeline(*, embedder, ...)`
用 alt embedder + code-friendly DispatchSplitter 配置。

### 2.5 CodeSplitter dispatch

`DispatchSplitter` 擴成:

```python
class DispatchSplitter(TransformComponent):
    def __init__(self):
        # ... existing markdown/sentence ...
        self.code_splitters = {
            ".py": CodeSplitter(language="python", chunk_lines=40,
                                chunk_lines_overlap=15, max_chars=1500),
            ".ts": CodeSplitter(language="typescript", ...),
            ".tsx": CodeSplitter(language="tsx", ...),
            ".js": CodeSplitter(language="javascript", ...),
            ".jsx": CodeSplitter(language="javascript", ...),
        }

    def __call__(self, nodes):
        for node in nodes:
            ext = Path(node.metadata.get("filename", "")).suffix.lower()
            if ext in self.code_splitters:
                yield from self.code_splitters[ext].get_nodes_from_documents([node])
            elif markdown:
                # ... existing ...
            else:
                yield from self.sentence_splitter...
```

語言不在表內(`.go`/`.rs`/...)走 SentenceSplitter。安全。

### 2.6 Parallel retrieval

`Retriever.search(query, collection_ids, ...)`:

```python
def search(self, query, collection_ids, ...):
    # 1. 分組:每個 embedder_id 一組 collection
    groups = group_collections_by_embedder(collection_ids, spec)
    
    # 2. 每組獨立 dense search
    dense_results = []
    for embedder_id, cids_in_group in groups.items():
        embedder = self._embedders[embedder_id]
        query_vec = embedder.embed_query(query)
        dense_results.append(
            self._dense_search(query_vec, cids_in_group, embedder_id)
        )
    
    # 3. BM25 over chunk.text (跨 group 可共用,text 不靠 embedder)
    bm25_ranked = self._bm25(query, all_chunks_across_groups)
    
    # 4. RRF merge: each dense list + bm25 → 一個 ranked list
    fused = reciprocal_rank_fusion([*dense_results, bm25_ranked])
    
    # 5. 既有 MMR + parent-doc merge 一樣跑
    return self._mmr_and_merge(fused, ...)
```

要點:
- BM25 不用換,文字相似度跟 embedder 無關
- Dense 部分每組獨立查、再用 RRF 合(分數不可比但 rank 可比)
- MMR 的相似度計算需要 vector → 同組內走自己的 embedder,跨組不算(限縮在組內)
- Parent-doc merge 跟 collection scope 無關,仍跨 group 合

實作:`_dense_search` 用 specstar 的 vector query API,加 `embedder_id` filter
條件 + 對應的 `embedding` 或 `embedding_alt` 欄位。

### 2.7 Endpoints

**新增**:

```
POST /kb/collections
  body: { name, description?, icon?, 
          git_url?, git_branch?, git_token?, 
          embedder_id?, sync_interval_hours? }
  → create Collection;若有 git_url → 觸發背景 sync 一次

POST /kb/collections/:id/sync
  → 觸發手動 sync(git pull + re-ingest 變更的檔案)。同步等待完成,回 last_sha
```

修改:
- `GET /kb/collections` 回應加上 git_* 跟 embedder_id 欄位(已存在資料就是 None,既有 doc collection 不影響)

### 2.8 Background sync scheduler

App 啟動時 `asyncio.create_task(_sync_scheduler(spec, ingestor, code_repo, settings))`。
迴圈:每 `sync_check_interval_sec` 秒,掃所有 collection,挑出
`git_url IS NOT NULL AND sync_interval_hours IS NOT NULL AND last_pulled_at + sync_interval_hours < now`
的,跑 sync 流程。

### 2.9 P3.0 範圍(checklist)

- [x] Deps:`tree-sitter-languages 1.10.2` + `tree-sitter<0.22` 加進 pyproject
- [x] `Collection` schema 加 `git_url/git_branch/git_token/git_last_sha/git_last_pulled_at/embedder_id/sync_interval_hours`
- [x] `DocChunk.embedding` 改 nullable + `embedding_alt` (dim=`CODE_EMBED_DIM`)
- [x] `Settings` 加 `kb_code_embed_*` + `git_default_token` + `sync_check_interval_sec`
- [x] `factories.get_code_embedder()`(`build_code_pipeline()` 暫不需要 — 同一 pipeline 內 routing)
- [x] `kb/code_repo.py`:`CodeRepoIngestor.sync()` ephemeral clone + walk + ingest
- [x] `Ingestor` 雙 embedder(根據 collection `embedder_id` 寫對欄位)
- [x] `DispatchSplitter` 加 CodeSplitter dispatch(`.py/.ts/.tsx/.js/.jsx`)
- [x] `Retriever`:per-field dense fan-out + 既有 RRF 合併(`code_embedder=` 注入時自動雙路)
- [x] `kb_routes.py`:`POST /kb/collections` 接 git 欄位、`POST /kb/collections/:id/sync`
- [x] Background scheduler:`CodeRepoSweeper.tick()` + lifespan `_code_sync_sweeper()` 已內含
- [x] Tests(實際命名 / 落點):
  - `tests/kb/test_li_pipeline.py::test_dispatch_splitter_routes_python_to_code_splitter`
  - `tests/kb/test_code_repo.py`:clone + last_sha + branch + OSError skip + splice_token + bogus URL
  - `tests/kb/test_dual_embedder.py`:embedder_id 路由 + factory(None/有/無)
  - `tests/kb/test_dual_retriever.py`:雙欄位 fan-out 計次 + HyDE alt branch
  - `tests/kb/test_code_sweeper.py`:tick due/not-due/manual-only/fail-isolation
  - `tests/api/test_kb_code_routes.py`:create with git_*、sync 200/400/404/502、lifespan sweeper
- [x] 100% backend coverage(`kb/code_repo.py` + `kb/retriever.py` 100% 行+分支)、ty + ruff 全綠

### 邊界(P3.0 不做)

- ❌ FE 任何改動(另外 PR;P3.1)
- ❌ Cross-file reference(P3.1 用 softlink prepend)
- ❌ webhook
- ❌ Git submodule 處理(submodule 不 recurse,只 clone top-level)
- ❌ LFS(big binary 不在 v1 範圍)
- ❌ Git SSH key(只支援 HTTPS + token)
- ❌ Branch 切換 / 多 branch 並存(一個 collection 一個 branch)

---

## 3 · P3.1 — 排程 + 跨檔 + FE

### 3.1 排程 sync scheduler

`_sync_scheduler` 背景 task。可開可關 per-collection。

### 3.2 Cross-file reference(softlink prepend)

Ingest code 時,parse 每個檔案的 `import` / `require` / `from ... import`:
- Python `import foo.bar` / `from foo import baz`
- TS/JS `import { X } from './path'` / `require('./path')`

把這資訊 prepend 到該 chunk 的 text:

```
imports: foo.bar, baz from foo
---
def my_function(...): ...
```

效果:semantic search「哪裡 import foo」會命中,即使函式 body 沒提到 foo。便宜
且有用。**不建立額外 resource**。

### 3.3 FE

- `New collection` modal 加分頁:「Documents」 / 「Code from git」
- Git 分頁表單:URL / branch / token(masked input)/ sync interval
- Collection card 加「Sync now」按鈕 + 顯示 `last_sha` + `last_pulled_at`
- KB chat scope picker:勾 collection 時不需要區分 embedder(retriever 內部處理)

---

## 4 · P3.2(未來)

- LSP 跨檔 reference(`pyright` / `tsserver`)— 精確但複雜
- Code-specific Reranker(`Salesforce/SFR-Embedding-Code` 之類)
- 更多語言:`.go`/`.rs`/`.java`/`.c`/`.cpp`(各 ~3 行)
- Submodule recursion
- Git SSH key auth
- Multi-branch per collection

---

## 5 · Open questions(P3.0 開工時要回答)

1. `CodeSplitter` 的 `chunk_lines` / `chunk_lines_overlap` / `max_chars` 預設值要不要 per-language 調?v1 全部 40/15/1500
2. Token rotation:Collection 的 `git_token` 過期怎辦?v1:sync 失敗 → status 翻 `error`,FE 顯示 "token expired"。Re-create collection 就好(替換 token)
3. 既有 `EMBED_DIM` 常數 vs 新的 `kb_embed_dim` 設定:v1 保留常數(從 settings 取 dim 後在 register_all 時 set),不破 import
4. `tree-sitter-language-pack` 跟 `llama-index-core` 的版本兼容:LI 0.10 的 CodeSplitter API 跟 language-pack 0.x 對齊;鎖版本確認

---

## 6 · 風險

| 風險 | 影響 | 對策 |
|---|---|---|
| `git_token` 明文存 db | 內部威脅 / 備份外洩 | v1 假設 db 在受控環境;deploy.md 明寫;v2 加 secret encryption |
| `CodeSplitter` 對 invalid syntax 行為(half-edit 中的檔)| 抛例外或切爛 chunks | 包 try/except,fail-soft 降級到 SentenceSplitter,log warn |
| Repo 太大(>1GB)clone 慢 | UX 卡 | `git_clone_timeout_sec` 上限 5min;超時 → fail + 提示 |
| Tree-sitter language-pack 跨 platform 兼容 | 部署到非 Linux 出錯 | CI 在 ubuntu + macOS 都跑 install;若 windows 要支援再說 |
| Parallel retrieval 變慢(2 倍 embedder 調用)| 搜尋延遲 | 兩個 embed_query 並行(`asyncio.gather`);bge-m3 + nomic-code 加總 <200ms |
| Self-hosted gitlab 用內部 CA | 預設 CA bundle 不認 | `GIT_SSL_CAINFO` env;deploy.md 寫設定方式 |

---

## 7 · Out of scope(P3 整體)

- ❌ GitHub Enterprise(可能也能用,但未測;沒承諾)
- ❌ Bitbucket / Gitea / 其他(同上)
- ❌ Per-user PAT(都是 collection-level)
- ❌ Branch switching / multi-branch indexing
- ❌ Image embedding(P3.2 才考慮 CLIP)
- ❌ Code execution / interpretation(不是 KB,是 sandbox 的事)
