# Plan — #104 收尾:chunk 綁內容、去除 `source_doc_id` 依賴、拔除 re-home

> Grill 收斂(2026-07-07)+ 對抗式部署風險稽核(workflow `survey-104-remove-source-doc-id-risk`,
> 32 agents / 17 確認風險)後定稿。承接 PR #490(dedup P1+P2)、PR #491(fan-out gate +
> retrieval orphan 防呆 + write_cache 護欄),兩者在 `master` 但**尚未部署**。
> 線上 = release `v2026.07.06`(commit `fd6bef2d`),**pre-#104,DocChunk schema = v3,
> 既有 chunk 連 `source_file_id` 欄位都沒有(decode 成 "")**。部署 = 一次 v3→v5。

## 稽核判定:**GO-WITH-CHANGES**

架構方向正確,但原計畫「硬切 file_id + 移出 index」照出貨會造成兩個**必然事故**;
以下兩個鎖定修正零成本一次解掉 R1/R3/R4/R5:

1. **解析用 coalescing fallback,不硬切** — chunk→doc 解析:`source_file_id != "" → (collection, file_id)`;
   否則退回 `source_doc_id`。既有 v3 chunk 仍保有可讀的 `source_doc_id` 且仍指向存活 doc,
   fallback 零成本消除檢索空窗,並拆掉 runbook「reindex 必須緊接部署」的脆弱時序依賴。
2. **只拔 Ref + cascade,保留 `source_doc_id` 於 `indexed_fields`** — 物理 drop 延後另 PR。
   Ref 與 index 正交;保留 index 使 `migrate/execute` 冪等非破壞、殘留查詢不退化、刪除鍵仍可用。

## 核心不變式

- Chunk 認**內容**:key = `(collection_id, source_file_id)`;回 doc(有真 sfid 時)=
  `select doc where collection_id==c and content.file_id==sfid`(**一對多** = 去重前提)。
- **過渡期**:`source_file_id==""` 的 legacy chunk 一律退回 `source_doc_id` 解析。
- **Canonical doc**(需單一代表時):同 `(collection, file_id)` 取 `created_time` 最早、`resource_id` tiebreak。
- 刪除:**refcount**(存活的同 `(collection, file_id)` doc 數)==0 才刪內容 chunk set。
- 不再有 owner / alias 之分——同 file_id 的每份 doc 都是 peer。

## 決策鎖定(grill Q1–Q7 + 稽核修正)

| # | 面向 | 決定 |
|---|---|---|
| Q1 | 刪除 | 同步 best-effort **refcount** 刪 + 週期 sweep 兜底 + 保留 #491 檢索防呆;移除 re-home |
| Q2 | 併發 | best-effort gate,key `(c,file_id)`;偶發重複交 sweep 折疊,不加 CAS |
| Q3 | citation | 單一 canonical doc,UX 不變;多路徑展開(原 P3)另議 |
| Q4 | chunk 數 | `count(chunk where source_file_id == doc.file_id)`;page agg `GROUP BY source_file_id` scoped |
| Q5 | 既有資料 | 搭「部署必跑的 reindex」順風車 stamp `source_file_id` + 折疊重複;零 backfill code |
| — | 欄位退休 | `source_doc_id` → `str=""`(**只拔 Ref+cascade、保留 index**);移除**讀取引用**;物理 drop 延後 |
| ★ | 解析 | **coalescing fallback(`sfid or source_doc_id`),非硬切** — 由稽核升為鎖定 |
| Q6 | dedup peer 顯示面 | 仍 copy text/preview/token_count,來源改「任一同 `(c,file_id)` peer」 |
| Q7 | 切分 | 一個 PR、flat phases |

## 事實根據(已驗證)

- `DocChunk`(`resources/kb.py:355`):`collection_id: Ref(cascade)`、
  `source_doc_id: Annotated[str, Ref(source-doc, cascade)]`、`source_file_id: str = ""`。
- `content.file_id = xxh3_128_hexdigest`(`ingest.py:66-68`)**恆為 32 hex、絕不為 ""** ⇒
  sfid="" 是永不與真 hash 相撞的 sentinel(#490/#491 對它 by-design 安全,稽核已驗)。
- `_reindex_only`(`resources/__init__.py:99`)是 **identity**——不 stamp sfid;sfid 只能靠 **reindex** 補值。
- **specstar Ref 關聯非持久化**(boot 由 `extract_refs` 從 annotation 重建);拔 Ref 即不裝 cascade;
  msgspec 依 base type `str` decode ⇒ **Ref→str 移除在既有 v3/v4 資料上零遷移風險**(稽核判 not-a-risk)。
- specstar schema step 只拿單一 record、**不能 join** ⇒ sfid backfill 不能用 Schema.step ⇒ 靠 reindex。
- `rehome_shared_chunks`(`kb/ingest.py:80`)+ 2 呼叫點(`kb_routes.py:1748` 刪、`1810` 搬)。
- FE 對 **chunk** 的 source_doc_id 無邏輯消費;只有 `web/src/autocrud/generated/*`(自動生成)。

## Flat phases(每 phase 一 commit,走 `/tdd`)

- **P1 ✅ retriever 輸出解析走 coalescing resolver(已完成,本 commit)** — 新增
  `Retriever._resolve_doc_id(chunk)` / `_canonical_doc_id(c, file_id)`:`sfid!="" → (c,file_id)` 的
  canonical(created 最早 + rid tiebreak),否則退回 `source_doc_id`;**true orphan(兩路皆 miss)才 drop**。
  套用到:citation `document_id`/`filename`(scored loop)、canonical text(經 document_id)、quality prior
  `q_of`。測試:file_id 救回 dangling source_doc_id、canonical=最早、legacy fallback、true-orphan drop、
  quality follows canonical。全 `tests/kb/` 925 綠、ty/ruff 淨。
- **P2 其餘讀取切 coalescing** — `chunk_counts.py` ✅(Q4 per-content,collection-scoped GROUP BY +
  legacy source_doc_id fallback,已完成)。剩:overlay shadow(`retriever.py:291`,R7)、
  `kb_routes.py:1838` `list_doc_chunks`(R5,FE 消費者)、provenance filter(#263,`LocationFilter`
  接 file_id,plumbing 到 `agent/tools.py`)。全走 coalescing(保留 legacy source_doc_id 相容)。
  **範圍決定**:`exclude(#308)` 保留(權限語意超範圍);`quality_coordinator` peer 情境改由 P4 的
  peer-inheritance 處理(避免重複 LLM 評分)——因 P1 已讓 retrieval quality 走 canonical,peer 顯示
  neutral 無害。**這些延後之所以安全:見 P3 keep-writing 決定。**
- **P3 刪除改 refcount + 欄位半退休** — `source_doc_id` 註解 `Annotated[str, Ref(cascade)]` → **`str = ""`**
  (**保留於 `indexed_fields`**);**★關鍵:ingest 仍持續「寫入」`source_doc_id=doc_id`**(只拔 Ref/cascade +
  不再依賴它解析),使所有殘留 source_doc_id 讀取在 post-P3 仍運作(僅 aliased peer 查自己=0,而 peer 僅
  reindex 後存在);physical drop + 停寫延後另 PR。schema **append** `.step("v4", _reindex_only, to="v5")` +
  `Schema(DocChunk, "v5")`(**延長鏈、不可重寫**)。delete/move 兩處 `rehome_shared_chunks` → refcount 刪;
  移除 `rehome_shared_chunks`。測試:刪父 doc → 獨佔 chunk 被 refcount 收、共享 chunk 因 sibling 存活而留;
  **R9 回歸**:舊 v3/v4 row → v5 read/migrate 不 raise(照 `test_token_count.py:45`)。
- **P4 gate 改 `(c,file_id)` + 刪除語意分離** — `_alias_to_existing_content`/`alias_if_duplicate` 只看
  `(collection, file_id)`(保留自身 chunk 排除 guard);dedup peer text/preview 從任一 peer 取。**拆兩支刪除**(R2):
  `_delete_own_chunks(doc_id)`(現 `_delete_chunks`,per-doc)沿用於 pre-write/alias-partial(304/463/545/1051/1153);
  `_delete_content_chunks_if_orphan(c, file_id)` 僅 refcount==0 分支呼叫,`WHERE source_doc_id∈docs OR source_file_id==f`(UNION,涵蓋過渡期 sfid="")。
- **P5 orphan + dup sweep** — 週期掃:同 `(c,file_id)` 多組 → 折疊成一;`sfid!=""` 無存活 doc → 刪;
  `sfid==""` legacy → 僅當 `source_doc_id` 無法 resolve 到存活 doc 才刪(R6)。#491 檢索防呆保留。
- **P6 FE 型別重生成 + docs** — 重跑 autocrud 生成(**預期良性純加法 diff**,R10);CHANGELOG [Unreleased];
  修 `resources/__init__.py:358-360` 的 #263 comment 加註「reindex 前勿對 doc-chunk 跑 migrate」。

## 部署 runbook(修正版)

> 前提:P1 fallback + P3 保留 index + P4 拆刪除 + schema v5 step 全在同一 PR。

- **步驟 0(部署前,CI/staging)**:100% gate 綠;確認 R1 fallback 測試、R9 mixed-schema 回歸、
  R7 overlay dedup-peer probe 測試存在;staging 用 v3 fixture DB 跑檢索,確認 fallback 命中**非空**。
- **步驟 1 — merge + 部署**:線上一次 v3→v5(lazy read-time identity migrate)。**驗證點**:部署後
  **立即**對既有 collection 下 query / `ask_knowledge_base`,確認**檢索非空**;空 = fallback 沒生效 → **回滾**。
  確認 `migrate/execute` 未被觸發。
- **步驟 2 — operator `reindex_collection` 全 collections**(stamp sfid + 去重):因 fallback 已消空窗,
  **reindex 不再有時效壓力**,可分批監控。**驗證點**:抽查原 pre-#104 doc chunk `sfid!=""`;
  抽查已知重複內容折疊成單一 set;監控無 R2 症狀(去重內容 chunk 被誤刪)。
- **步驟 3 — 完成確認**:`count(chunk where source_file_id=="")` 趨近 0。
- **步驟 4(延後 PR,線上穩定後)**:物理 drop `source_doc_id` 欄位 + index、移除 fallback。
  **前置**:步驟 3 確認 sfid="" chunk 已清乾淨。

**runbook 硬性警告**:**全站 reindex 完成前,絕不可對 doc-chunk 執行 `POST /doc-chunk/migrate/execute`**
(它會把 sfid 重抽成 "",brick 掉過渡期補救,R4)。

## 稽核推翻/降級項(免過度防禦)

- specstar `Ref→str` 移除 = **not-a-risk**(關聯非持久、boot 重建;msgspec 依 str decode)。
- #490+#491 首次跑 pre-#104 資料 = **by-design 安全**(sfid="" sentinel 永不撞真 hash)。
- 「移出 index → 全表掃描 the hang」= 反駁(且保留 index 後全 moot)。
- 100% coverage gate 對過渡態 = not-a-risk(但務必補 R9 測試)。
- provenance filter 語意 = 承諾未破(content-level、collection-scoped);相對 v4 base 反而改善 recall。

## 延後(各自另 PR)

- 物理 drop `source_doc_id` 欄位 + index + 移除 fallback(線上穩定 + sfid="" 清乾淨後)。
- citation 多路徑展開(原 Q3 path-visible,與 #485 相關)。
