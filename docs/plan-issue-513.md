# Plan: Defect Library — 影像缺陷知識庫 + 分類輔助 (#513)

> **#513**:現場有成千上百種 defect type,判斷邏輯散在各站點 case-by-case 的程式
> (kernel,rule + ML)裡,沒有整理過的知識層、也沒有 AI 輔助工程師判斷「這張圖是哪一種
> defect」。延伸自 #104。**本 plan 為 `/grill-me` 收斂後版本**——範圍大幅縮小:不開新 App、
> 沿用 ContextCard、砍掉自動產線流程與 measurement 整合,只剩兩個 user-driven 入口。

## Grill 收斂的關鍵決定

1. **落地形態**:不開 App。defect-lib = 一個 **KB collection** + tools,掛在現有 KB chat / 介面。
2. **entry 身分**:沿用現場既有 defect code;**code 是 per-station(machine 粒度)**。
3. **entry 儲存**:沿用 **`ContextCard`**(不加 resource)。station 靠 **multi-key** 進 `keys`
   (list):`machine|code` / `type|code` / `layer|code`。查詢把一台機**展開成 scope 鏈**
   `[machine, type, layer, global]`,**由具體到廣、第一個命中者勝**(override 自然贏)。
   知識**共用先行**(多半 type/layer 一張卡覆蓋整型機台),機台特有靠 machine 層 key override。
4. **scope 展開來源**:先由 **user 提供**站點+層級(圖本就 user 帶進);機台→type→layer 拓撲
   之後有了再自動化。
5. **用例塌成兩個(皆 user-driven)**:
   - **C 查代號**:問「這個 code 是什麼、標準、範例圖」→ 直接查 entry。
   - **B 上傳圖**:一張圖 + scope/context(+ **選配** user 貼的 indices)→ 視覺分類。
   - **用例 A(系統自動餵產線圖 + 現成 indices)砍掉**:沒有 image_id、圖→run 反查不到、
     indices 只能 user 手動給。⇒ **零 measurement 系統整合**;`get_kernel_indices` by-id 工具不做。
6. **index/rule**:entry 保留機器臉 `rule`,但只評估 **user 選配貼進來的 indices**(低優先)。
7. **image embedding 加法接入**(外包他隊):`ImageEmbedder` = **image 為核心**
   (`embed_documents` / `embed_query_image`)+ **選配** `embed_query_text`(他隊給 CLIP 就實作、
   不給就回未實作)。不綁他隊架構。
8. **flywheel 把關**:複用 **#377 DocQuestion**(B 沒把握就發問、答案回灌)+ **cardgen 卡提案**
   (動到 entry 一律 proposal→人核 commit;因為 entry 就是 ContextCard)。labeled 樣本自動累積。

## 現況查證(為什麼加法可行)

- **collection 沒有 `kind` 欄**(`resources/kb.py:113`);docs/code 是「長出來的」
  (`git_url` = 來源、`embedder_id` = 向量欄)。⇒ image 不必發明 discriminator。
- **`DocChunk` 早有 `embedding` + `embedding_alt` 兩個可空向量欄**(`kb.py:411-412`),
  各自固定維度/欄。⇒ 加第三個 `embedding_img` 是純加法,與描述向量並存同一筆。
- **retriever 合流吃 N 條 arm**;code-embedder → `embedding_alt`(`retriever.py:346-357`,
  `if self._code_embedder is not None:`)是 image arm 的**現成模板**,`None`-gated。
- **`Embedder` protocol 是純文字**(`kb/embedder.py:25`)⇒ 需兄弟介面 `ImageEmbedder`(bytes 進)。
- **圖今天只透過 VLM 描述被索引**(`parsers/vlm_image.py`;原圖 bytes 留 `SourceDoc.content`)
  ⇒ 這就是「交付前」狀態,現在能動;圖向量疊上、不取代描述。
- **ContextCard lookup 是 collection-scoped、`norm_keys` 精準 element membership**
  (`kb/context_cards.py`)⇒ multi-key scope 鏈用既有 `.contains` 就成立。

## 目標

一個以現有 KB 為底的 defect library,支撐 C/B 兩個 user-driven 用例,image embedding 模型做成
可抽換的可選元件(交付前純文字/VLM、交付後多一條圖向量 arm)。

## Phases(flat integer、TDD)

- **P1 · entry 模型 + defect-lib collection —— 出用例 C**
  - 建 defect-lib collection;entry = ContextCard,`keys` 用 scope-qualified 形式
    (`machine|code` / `type|code` / `layer|code`),`norm_keys` 派生;body 放 morphology + 白話
    判斷標準,參考圖為連結的 SourceDoc(原圖 render)。
  - 查詢:給 code + user 提供的 scope → **展開 scope 鏈、由具體到廣查 `lookup_glossary`,
    第一個命中者勝**;模糊走 `kb_search`。
  - 手建/cardgen 半自動種子 entry 3~5 筆(含共用卡 + 一個 machine override 驗 precedence)。
  - Unit:scope 鏈展開 + 最具體者勝、共用卡覆蓋整型機台、override、模糊查、參考圖 render。
  - **DoD**:工程師問「code X(某站)是什麼、標準、範例圖」→ 秒回。零 image 模型、零 measurement。

- **P2 · image 加法插座(空的、零行為變化)**
  - `resources/kb.py`:`IMG_EMBED_DIM` + `DocChunk.embedding_img`(可空 `Vector`)+ `CachedChunk` 鏡像。
  - 新 `kb/image_embedder.py`:`ImageEmbedder` protocol(`dim`/`identity`/`embed_documents(images)`/
    `embed_query_image(image)` + **選配** `embed_query_text(text)`,capability-gated)+ 測試 stub。
  - `kb/retriever.py`:`image_embedder: ImageEmbedder | None = None`;per-query + HyDE 兩處各 append
    一條 `_dense(field="embedding_img", …)`(照 `:346-357`);`_chunk_vec` 納圖向量;text-query arm
    只在 `embed_query_text` 有實作時才掛。
  - `api/app.py` / `factories.py`:`create_app(kb_image_embedder=…)`(今天 `None`)。
  - Unit:`None` 時輸出與現況逐字相同;注 stub 多一條 ranked list;text-arm 依 capability 開關;
    `embedding_img` 與 `embedding` 並存。
  - **DoD**:插座就位、行為零變化。他隊模型有明確落點。

- **P3 · 上傳圖初判(VLM 路徑)—— 出用例 B**
  - 上傳圖 + user 提供 scope/context(+ **選配** 貼 indices)→ 展開 scope 鏈硬過濾候選 entry →
    粗篩 shortlist → **VLM 看查詢圖 ↔ 候選參考圖直接比對**(reuse `VlmDescriber`)→ 排名 + provenance。
  - **選配**:user 有貼 indices → 評估 entry `rule`,融合進排名(低優先,可延後)。
  - 沒把握 → 走 **#377 DocQuestion** 向工程師發問;確認/更正 → **自動存 labeled 樣本**
    + 動到 entry 走 **cardgen 卡提案 → 人核**。
  - Unit:scope 硬過濾、shortlist、VLM 比對排名(mock VLM)、#377 觸發、labeled 落地、卡提案。
  - **DoD**:上傳圖 → 排名候選 + 依據 + 可確認。**live canned check**(真 VLM 一張圖)。
  - ⚠️ 期待值:交付前 B 主要是**粗 triage + 累積標註**;細分近似缺陷的準度要等 P4 的圖向量。

- **P4 · image embedder 接上**(gated on 他隊交付)
  - 實作 `ImageEmbedder`(他隊模型 adapter),`create_app` 從 `None` 換真的。
  - `kb/ingest.py`:圖檔在 VLM 描述外**多算一步**圖向量填 `embedding_img`(VLM 描述留著)。
  - **回填**:`re-index` 既有圖補圖向量(無 migration)。第三 arm 自動亮。
  - Unit:ingest 雙路徑並存、回填、`identity` 變更觸發重算。
  - **DoD**:以圖搜圖上線、純文字路徑不退化。**驗證**:標註圖搜、對的 type top-k 命中率。
  - ⚠️ 領域坑:generic CLIP 對 wafer/SEM 是 OOD,**八成要他隊用我方 P3 累積的標註圖 fine-tune**;
    交付含準確率驗證,非「算得出向量」即可。

- **P5 · 建庫閉環 + flywheel**
  - cardgen(#506)指向既有 defect 文件/圖庫 → 起草 entry(含 scope-qualified keys)→ keep/update/new
    → 人審 commit(冷啟)。
  - P3 的 labeled 確認 + #377 答覆 + 調參 → 卡提案回灌(動 entry 一律人核)。
  - Unit:草稿→去重→分級→commit;#377 答覆→提案;人核 gate。
  - **DoD**:library 邊用邊長,不需先手工整理完。

- **P6 · 混合檔(HTML/MD)+ 外部圖抓取 ingest —— 讓既有 HTML/MD 缺陷知識連圖進庫**(獨立,可先做)
  - **資料現實**(grill 定):既有缺陷知識在 HTML/MD,圖以**外部 http 連結**(`<img src>` /
    `![](url)`)夾帶,指向**內部影像伺服器**(後端連得到、**免認證 GET**)。單檔本身無圖 bytes、只有 URL。
  - **決定**:圖一律**保留 bytes、當一等公民**(**不**走 PdfParser「描述即丟」——否則混合檔的圖永遠
    上不了圖向量)。每張抓回來的圖 = 獨立 image SourceDoc → VLM 描述(現在)+ P4 可上 `embedding_img`。
  - **沿用 archive 展開接縫**:`store` 對 zip 已「一員一 SourceDoc」(`ingest.py:411-431` +
    `_extract`/`_store_file`)。HTML/MD 上傳時把引用的圖**展開**成額外 member 存入;文字檔本身照舊被
    `HtmlParser` / markdown 文字路徑吃。⇒ 圖走既有 `VlmImageParser` 路徑,bytes 落 `SourceDoc.content`。
  - **SSRF 圍籬**(硬需求):新 `IImageFetcher`(ABC,`I<Name>`)——**只抓 config allowlist 內的 host**,
    其餘跳過(記 log,**不** silently 當成功)。`kb.image_fetch.{enabled,allowed_hosts,timeout}` 走 loader
    schema + `config.example`(不碰 secrets)。內網直接 GET;認證留**可注入 seam**(之後要 token 再接)。
  - **韌性**:某圖抓不到 / host 掛 → 該圖跳過、文字照進、記可見 note;一張圖失敗不炸整份 ingest。
  - **去重**:content-addressed(#104)—— 同一圖 URL 被多份文件引用、byte-identical → 自動 alias、不重存。
  - **linkage**:image SourceDoc meta 記來源(parent doc id + 原 URL),知識(文字+圖)可在 collection 回連。
  - Unit:img URL 抽取(HTML/MD 純函式)、allowlist 過濾、fetcher(mock)、展開產出 text+image 雙 SourceDoc、
    抓失敗韌性、去重 alias、**未設 allowlist → 不抓(行為逐字不變)**、linkage meta。
  - **DoD**:上傳一份 HTML/MD 缺陷文件 → 文字進庫、內部連結圖一併抓回成獨立可搜圖(VLM 描述),
    P4 到位後自動可上圖向量。**live check**:一份真文件 + 內網一張真圖。
  - ⚠️ 相依:抓圖當下影像伺服器要活+可達(**新增 runtime 相依**);host allowlist 是安全前提。

## 相依 / gating

- **P2 獨立**,可隨時做。
- **P4** gated on 他隊交付 `ImageEmbedder`(image-only 核心即可;text-query 選配)。
- **P6 獨立**,不依賴 P3/P4,可先做(是 library 冷啟 bootstrap 的主要入口);其產出的圖天生就是
  P4 圖向量的落點。
- **measurement 整合已移除**(A 砍掉),無外部系統相依。
- 主線 P1 → P3;P2 平行;P6 為 bootstrap 入口(可先);P4/P5 視相依插入。

## 待決 / 風險(已大幅收斂)

1. **機台→type→layer 拓撲**:先 user 提供 scope,拓撲有了再自動展開。
2. **entry `rule` 格式**(card body 內嵌 JSON/YAML 塊):index 是選配、低優先,格式細節 P3 再定。
3. **圖向量的領域適配**:他隊需用 P3 標註圖 fine-tune;ground truth 由 P3 累積。
4. **交付前 B 準度**:主要價值是 triage + 資料累積;細分準度靠 P4。

Related: #104 #106 #133 #377 #355 #506
