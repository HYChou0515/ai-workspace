# 備份機制 — 從「一份都沒有」到「config 怎麼設都不會漏備」

現狀:**這個系統沒有任何備份**。`kubernetes/base/` 下有兩個 CronJob(`cronjob-eval.yaml`、
`cronjob-graph.yaml`),兩個都只是 `curl` 打 API 丟一個 job 進去,都不是備份;
`docs/deployment.md`、`docs/migrations.md`、`kubernetes/README.md` 整份沒有提過備份或災難復原。

資料量級 **100 GB – 2 TB**(user 提供)。這個數字讓「每晚整包 tar」不成立,也讓
「還原要花多久」成為比「備份要花多久」更硬的約束。

**User 鎖死的需求:一個 general 的機制 —— 不會因為 config 設成什麼樣子就出錯或沒備份到。**
這句話是本計畫的軸,不是附註。下面 §3 的三條不變量全部是為了它存在的。

保的是兩種失效(user 確認):**(a) 整份沒了**(PVC / cluster / 儲存後端損毀)和
**(c) 壞 release 或壞 migration 把資料寫爛**。**(b) 使用者誤刪單一 collection 明確不在範圍**——
那是軟刪除 / 回收桶的產品題,用備份做意味著「把整包解到旁邊、手動撈、再塞回線上」,
這個操作在半夜出事時沒有人做得對。

---

## 1. 現狀(已查證,基準 `6488a7ac`)

### 1.1 耐久資料出口,完整清單

從 `config/schema.py` 把每個指定儲存位置的欄位列完,再回頭查它被誰消費:

| # | 出口 | 由哪個設定決定 | 內容 | 備份? |
|---|---|---|---|---|
| 1 | **specstar disk root** | `filestore.disk_root`(prod `kind: specstar`) | `{Model}/meta` + `{Model}/data` + `_blobs` | **要** |
| 2 | **sandbox NAS 樹** | `sandbox.durable.kind: nfs_tree` + `nfs_root` | 每個 item 的 workspace 工作檔,as-is | **要** |
| 3 | sandbox scratch | `sandbox.root`(`rca-scratch` PVC) | 活的工作目錄 | 不要 — 設計上可拋,idle reaper 回收進耐久層 |
| 4 | job queue | `message_queue.kind`(預設 `simple`) | `simple` ⇒ job 就是 specstar resource,已含在 #1 | 跟著 #1 |
| 5 | LLM call log | `observability.llm_log.dir`(預設 `logs/llm`,`keep_days: 0`) | 每次 outbound LLM 呼叫一筆 | 不是資料,但**無上限成長**,是另一筆帳 |

**#2 存不存在,由一個設定鍵決定,而預設是「不存在」** —— 這正是 user 那句需求的具體形狀。
`factories.py:411-437` 的 `build_sandbox_filestore`:

- `sandbox.durable.kind: ""`(**預設**)→ 直接 return API 的 filestore。sandbox 檔案以
  `WorkspaceFile` 記錄存進 specstar,bytes 落在 `_blobs`。**只有一棵樹。**
- `"nfs_tree"` → `NfsTreeFileStore(nfs_root)`,**兩棵樹**。
- `migrate_from: "specstar"` → M2 雙讀,**兩棵樹且互為 fallback**,只備一邊會漏掉還沒 backfill 的舊檔。

`kubernetes/base/configmap.yaml` 裡那整段 `SANDBOX_DURABLE_*` 是註解掉的。
**prod 實際是 `nfs_tree`、無 `migrate_from`(user 確認)**,所以是兩棵樹。

手寫一份路徑清單的人會在 `kind: ""` 這格出錯 —— 它不是「沒有 sandbox 資料」,是「跟 #1 同一棵樹」,
於是要嘛重複備份同一棵,要嘛兩棵都漏。**清單必須從 `factories` 導出。**

### 1.2 `/data` 長什麼樣

`backend.py:264-281` 的 `DiskBackendProvider`:

```
{rootdir}/
  {ModelName}/
    meta/   _sh/<ab>/<cd>/<pk>.data
    data/
      store/     _sh/<ab>/<cd>/<uid>/             ← 真正的 payload
      resource/  _sh/<ab>/<cd>/<rid>/<rev>/<ver>  ← symlink → store/<uid>
  _blobs/  _sh/<ab>/<cd>/<file_id> + .blobmeta + .refcount
           _sessions/_sh/<ab>/<cd>/<upload_id>/
```

實測(走 production wiring:`factories.get_spec` + `SpecstarFileStore`,`kind="specstar"`,
寫一個 4115 bytes 的 workspace 檔案,然後列出整棵 disk_root):

```
  symlink       0  workspace-file/data/resource/_sh/9c/af/…/v_v3
  file        150  workspace-file/data/store/_sh/ee/d7/…/data      ← 記錄,不含 bytes
  file        585  workspace-file/meta/_sh/9c/af/….data
  file       4115  _blobs/_sh/f7/2e/e8a2291ee2d2b0919bbc4475c788ea47   ← bytes 在這
  file         75  _blobs/_sh/f7/2e/….blobmeta
  file          1  _blobs/_sh/f7/2e/….refcount

files whose bytes CONTAIN the payload: ['_blobs/_sh/f7/2e/e8a2291ee2d2b0919bbc4475c788ea47']
```

三件讀程式碼看不出來、實測才知道的事:每個 blob 是 **3 個檔案**(blob + `.blobmeta` + `.refcount`);
`resource/…/v_v3` **真的是 symlink**;路徑裡的 `/` 被換成 **`∕`(U+2215)**,檔名含非 ASCII。

⚠️ 這三件事在最終方案裡**都不重要** —— 見 §2,我們不碰這個佈局。留在這裡是因為它們是
「如果有人想改用檔案層級複製」時會踩到的地雷,值得寫下來一次。

### 1.3 `_blobs` 是 content-addressed 且 write-once

`blob_store/simple.py:617`:

```python
file_id = key if key is not None else xxh3_128_hexdigest(data)
```

:626-629 的判斷 `if key is not None or not self._blob_exists(safe_name)`,註解寫明
「content-addressed (hash) keys are immutable, so skip the write when the blob already exists」。

全 repo 每一處 `Binary(...)` 建構(`kb/import_jobs.py:259`、`kb/ingest.py:818,984`、
`kb/wiki/store.py:202,243`、`api/kb_routes.py:2747`、`filestore/specstar_impl.py:155,281,288`)
**都沒有傳 `key=`**。所以 `_blobs` 是 append-only:改一個檔案是產生**新** blob,舊的變孤兒等 GC。

開了 `nfs_tree` 之後,`__main__.py:213` 把 `sandbox_filestore`(NAS 樹)傳給
`create_app(filestore=...)`,`api_filestore` 只剩「註冊 `WorkspaceFile` 模型」和「當 M2 fallback」
兩個作用,沒有東西寫它。所以 **`_blobs` 裝的是 KB 和 wiki 的內容**(`SourceDoc.content`、
`preview`、`ImportJob.archive`、`WikiPage.content`),不含 workspace 工作檔。

### 1.4 blob GC 的時間窗

`config/schema.py:316-318` + `filestore/blob_gc.py:5-6`:

```
gc_interval_sec: 3600.0   # 每小時問一次
gc_t1: "1h"               # 變成孤兒後的寬限,才進 quarantine
gc_t2: "24h"              # quarantine 裡的可逆停留,之後才真刪
```

blob 從「變成孤兒」到「永久消失」有 **t1+t2 = 25 小時**,中途「restores any still referenced」。

### 1.5 已經存在的東西:collection archive

`docs/collection-archive.md` 的 collection archive(zip:文件 + context card + 連結)是
**單一 collection 的可攜格式**,匯出/匯入同一套。它解的是「把知識庫從一個部署搬到另一個」和
「使用者自助救回一個 collection」,**不是平台層備份** —— 它不含 workspace 檔案、不含對話、
不含 App item、不含排程,也沒有排程與保留期。

本計畫不取代它,也不併入它。兩者的關係寫進 `docs/deployment.md`,避免將來有人以為有了一個就不用另一個。

---

## 2. 為什麼是 specstar 自己的 `dump`,不是複製檔案樹

specstar 有第一級的備份機制,**這是本計畫最重要的一個決定**:

| 東西 | 位置 |
|---|---|
| `rm.dump(query=...)` → `MetaRecord \| RevisionRecord \| BlobRecord` 串流 | `types.py:2131` |
| `load_record` / `load_records_bulk`,`OnDuplicate = overwrite \| skip \| raise_error` | `types.py:2209,2235` |
| `.acbak` 格式:`Header → ModelStart → Meta/Revision/Blob… → ModelEnd → Eof` | `resource_manager/dump_format.py:32-71` |
| `SpecStar.dump(bio, model_queries)` | `crud/core.py:3484` |
| `backup = dump \| load \| migrate` 權限範圍 | `types.py:1168` |

用它而不是自己複製 `/data` 的理由:

1. **`BlobRecord` 帶 `blob_data: bytes` inline**,封存是自足的 —— 沒有「共享 `_blobs` 不屬於任何 model」
   的問題,也沒有「記錄和 blob 要分兩趟、順序錯了就懸空」的問題。
2. **symlink / U+2215 檔名 / sharded 佈局全部不用管**,因為不碰內部佈局。
3. **時間窗過濾是原生的** —— `ResourceMetaSearchQuery` 有 `updated_time_start` / `updated_time_end`
   (`query_types.py:263-266`),增量不必靠外部工具。

### 2.1 在我們的後端上,dump 這半確實串流

`resource_store/simple.py` **沒有**實作 `dump_all_revisions`(grep 計數 = 0;只有 `s3.py:431` 有,
base 的預設 `basic.py:1750-1758` 回 `None`)。所以 `dump_resources_bulk` 回 `None`,
走 `resource_manager/core.py:4405` 的 slow path:一次一個 resource 讀、yield、丟掉。
`DumpStreamWriter.write`(`dump_format.py:113-116`)每筆 record 就 `bio.write`,不囤積。

另外 `resource_manager/core.py:4373` 是
`q = msgspec.structs.replace(query, limit=2**31 - 1, offset=0)` —— specstar 在 dump 時自己把
limit 蓋掉,所以時間窗匯出**不會**被 `SPECSTAR_DEFAULT_QUERY_LIMIT`(`query_types.py:182-184`,
未設時 fallback `2**32-1`)靜默截斷。

### 2.2 兩條路不能走

- **HTTP 路由不能用。** `crud/core.py:3272` 先 `buf = _io.BytesIO()` 再 `dump(buf)`,整包進記憶體。
  備份必須 **in-process** 呼叫 `spec.dump(open(path,"wb"))`。
- **`/_backup/*` 現在沒有授權(見 §4)。** 這是止血項,不是備份項。

---

## 3. 三條不變量

這三條是 user 那句「不會因為 config 設怎麼樣就沒備份到」的可執行版本。

### 不變量一:涵蓋 == app 實際寫入

> 在任何一個合法的 config 下,備份涵蓋的持久化出口集合 **等於** app 實際會寫入的持久化出口集合。
> 不相等時備份 **失敗**,而不是少備一份。

前例是 blob-gc,`filestore/blob_gc.py:39-40` 的註解就是這句話:
「a visible GC outage instead of a silent loss」。三層做法照抄:

1. **來源清單從同一份 `Settings` 導出**,不在 CronJob YAML 手寫路徑。
   對應 `worker/__init__.py:61` 的 `API_REGISTRY_JOBTYPES` —— blob-gc worker 用 API 自己的
   composition 建 registry,讓兩邊「by construction」相等。
2. **遇到不認得的 kind 就炸。** 對應 `worker/__init__.py:67`
   「fail loud rather than idle silently on a queue nothing feeds」。
3. **窮盡性測試**:列舉每個 kind 欄位的所有合法值(`filestore.kind` ∈ {memory, specstar}、
   `sandbox.durable.kind` ∈ {"", nfs_tree}、`message_queue.kind` ∈ {simple, rabbitmq}),
   斷言每種組合都有 handler。少一個 → **CI 紅**,不是上線後靜默漏備。

### 不變量二:切片大小界住還原所需的記憶體

`crud/core.py:3588-3590` 的 `meta_buf` / `rev_buf` / `blob_buf` **累積整個 model**,
只在 `ModelEndRecord`(`:3609`)才 flush。`BlobRecord.blob_data` 是完整 bytes,
所以**還原一個 model 需要的 RAM = 該 model 的資料總量**。

因此切片不是效能選項,是**正確性需求**:每片一個 `.acbak`,`ModelEndRecord` 提早到來,
`load` 的 buffer 被切片大小界住而不是被資料總量界住。時間窗切片同時就是增量。

⚠️ 這條約束**沒有任何東西強制它** —— 它靠測試和 runbook 維持。specstar issue #450 的 S1
修好之後這條可以降級為「純增量手段」。

**切片只適用 specstar,工作檔案樹不切。** 這一條是實作時才發現的,而且切樹是**錯的**:
full 的時間窗下界是從 specstar 最舊的那一列導出來的(`_earliest_updated`),而一個工作檔很容易
比那一列還老 —— 切過的樹會靜靜地漏掉每一個早於「資料庫第一列」的檔案。而解 tar 是串流的,
本來就沒有 `load` 那種記憶體上界要界。所以樹是每趟一份:full 全收,增量收 mtime 落在窗內的。

**切片救不了的地板**:`filestore.max_file_size` 預設 2 GiB,單一 blob 是單一 record,
而 `DumpStreamWriter.write` 還要 `_encoder.encode(record)` 再複製一份 ——
峰值 RSS ≈ 最大單檔 × 2。備份 pod 的記憶體照這個訂,切再細都沒用。

### 不變量三:驗證看參照完整性,不看總數

**不能用「總數比上次少就失敗」** —— blob-gc 刪孤兒會讓總數合法下降,這種 guard 會在每次 GC 後
誤報,然後被調鬆,然後變成擋不住任何事的裝飾品。

正確的判準兩層:

1. **來源根目錄必須是 mount point**(`os.path.ismount`)。沒掛上的 NFS 路徑不是 mount point ——
   精確、零誤報,而且正好打中「NFS 沒掛上 → 備到一棵空樹 → 保留政策砍掉舊的」這個失敗。
2. **抽樣參照完整性**:從剛寫好的 snapshot 抽 N 筆帶 `Binary` 的 live record,
   確認它指到的 blob 在 snapshot 裡。**這個判準對 GC 免疫** —— GC 只刪孤兒,
   live record 指的 blob 依定義還在(`.refcount` 就是為此存在)。

這一條有具體的程式碼路徑撐著,不是原則主張:`resource_manager/core.py:4419-4432` 的
blob 讀取包在 `except Exception: pass`,**讀不到的 blob 被靜默跳過,dump 照樣「成功」結束**,
而且 `dump()` 沒有任何回傳統計。所以**不能拿 dump 自己的成功當證據**。

---

## 4. specstar 側的缺口

全部整理在 **https://github.com/HYChou0515/specstar/issues/450**(八項,S1–S8)。
與本計畫的關係:

| | 我們怎麼處理 |
|---|---|
| **S1** `load` 累積整個 model | **不等修**。用不變量二的切片繞過。修好後切片降級為純增量手段 |
| **S2 / S3** blob 讀取失敗靜默跳過 | **不等修**。用不變量三的獨立驗證覆蓋 |
| **S4** `/_backup/*` 無授權 | **P1 在 app 層止血**。`pyproject.toml:26` 釘死 `specstar[magic]==0.13.0a2`,上游修好也要 bump + 回歸才生效 |
| S5–S8 | 不影響本計畫(我們不走 HTTP 路由) |

---

## 5. Phase

- **P1 — `_backup/*` 授權止血。** 照 `api/app.py:1658` `_block_raw_permanent` 的既有形狀,
  在 `spec.apply` 前佔住 `_backup/export|import` 改走 superuser 檢查。
  測試必須對**未修版本驗紅**(下面 §6.1 的重現就是紅的來源)。
- **P2 — `durable_sources(settings)` + 窮盡性測試。** 純函式,無 I/O。不變量一。
- **P3 — `python -m workspace_app.backup`。** specstar 半走 `spec.dump` 串流落地;
  NAS 半走檔案層級;mount point 前置檢查;寫 receipt(涵蓋的時間窗 / model / 筆數 / bytes / 耗時)。
- **P4 — 切片。** `updated_time_start` 時間窗 + 切片大小上界的測試 + 全量/增量鏈的保留策略。
  不變量二。
- **P5 — 驗證與告警。** 抽樣參照完整性(把 snapshot `load` 進暫存 spec 比對)+
  寫 `Notification` row → 既有 `INotificationChannel`,收件人 `server.superusers` +
  dead-man sweeper(讀最新 receipt,超過 N 小時沒更新就寫 row,**把「缺席」變成「一筆存在的資料」**)。
- **P6 — k8s CronJob + Secret + overlay。**
- **P7 — `python -m workspace_app.restore`** + manifest 與當下 config 比對,不符拒絕還原。
- **P8 — stg 實際演練**,寫成可重跑腳本。含「一邊寫一邊備」實測 dump 的一致性。
- **P9 — `docs/migrations.md` 條目 + `deployment.md`。**

### 5.1 告警為什麼走既有的 `INotificationChannel`

「通知」是部署方自己實作的介面,這個 seam 已經存在:`factories.py:1296-1306` 的
`get_notification_channel(dotted)`,docstring 寫明「There is no bundled default because the
platform cannot guess a relay, a from-address, or a compliance regime」。備份程式裡不寫 SMTP。

一個細節要處理:`OutboundNotification.recipient` 是**平台 user id**
(`api/notification_delivery.py:65-67`),備份告警沒有「收件使用者」。
**收件人用 `server.superusers`** —— 那個集合已經存在、已經 threaded 進 `get_spec`,
語意剛好是「誰在運營這套系統」。好處是備份 job 完全不需要碰 channel:
它只要往 specstar 寫一筆 `Notification` row,API 既有的 `notification_delivery_sweeper`
負責交付與重試;同一筆也進 in-app 鈴鐺(docstring 說那是 record of truth),
email channel 掛了告警也沒消失。`Notification` 本身有 `dedup_key`
(`notification_delivery.py:61`),多個 pod 同時發現同一件事只會出一封。

### 5.2 為什麼是 CronJob 而不是 JobType

觸發方式 user 說隨意,只要求能承受大量 data。選 CronJob 直接做事(不是 enqueue):

- 和 `cronjob-eval` / `cronjob-graph` 同形狀,維運方認得。
- 有 `activeDeadlineSeconds` 和 `concurrencyPolicy: Forbid` —— 那才是給長時間單次工作用的。
- 行程結束就把記憶體還給系統,對不變量二那個「最大單檔 × 2」的峰值有實際差別。
- 失敗是叢集層級可見的事件。

⚠️ 順帶記一個**沒查清楚的矛盾**:specstar 的 queue 是心跳制,`basic.py:553` 用
`heartbeat_timeout_seconds = self._heartbeat_interval * 3`(`_heartbeat_interval = 5.0`,`basic.py:82`),
`basic.py:493-494` 明說有近期心跳的 job 會被跳過 —— 所以**心跳活著就不會被重投,沒有固定上限**。
但 `cronjob-graph.yaml` 的註解寫著「the tail ran past the 30-minute ceiling, where a job is
redelivered rather than finished and so never converges」。這兩件事對不起來,
那個 30 分鐘上限不在 queue 裡。**原因未查明** —— 這個未解的矛盾本身就是不把備份放上 queue 的理由。

---

## 6. 附錄

### 6.1 S4 的重現

用 `tests/api/conftest.py` 同樣的 composition 建 app,透過正常寫入路徑寫一個已知字串,
再以**無任何 header** 的請求索取:

```
GET /api/_backup/export  -> 200
  bytes returned: 60545
  contains the secret payload: True      ← 檔案內容真的被帶出來
repeat, no headers at all -> 200
POST /api/_backup/import (garbage) -> 400   ← 400 是格式錯誤,不是拒絕受理
```

為什麼擋不住:`crud/core.py:2019` 的 `_apply_backup_routes` 無條件註冊;
`api/app.py:1500` 是 `api = APIRouter(prefix="/api")`,**沒有 `dependencies=`**;
middleware 只有 CORS / 版本標頭 / perf trace;`__main__.py:122` 是
`get_user_id = lambda: settings.server.default_user`,app 內沒有認證層。
`api/app.py:1658` 那段用 first-match-wins 擋掉了 `/permanently`,但 `_backup/*` 不在清單裡。
而 `perm/checker.py:24-27` 記著 `permission_checker` 槽位被 `AllowAll()` 遮蔽,部署方想自己補也補不上。

⚠️ **外部是否擋得住取決於 gateway,那個看不到。** 但叢集內這條路看起來是通的 ——
`cronjob-graph.yaml` 的註解自承「this POSTs unauthenticated to the in-cluster ClusterIP service」,
**而 sandbox 是在跑使用者的程式碼的**。

### 6.2 還沒查、也還沒決定的

- **`/data` 與 NAS 樹的實際大小、檔案數。** 只有「合計 100 GB – 2 TB」這個 user 提供的數字。
  P3 之前要在 prod 量:`du -sh /data`、`du -sh /data/_blobs`、`find /data -type f | wc -l`。
  切片大小要照這個訂。
- ~~**dump 在 app 持續寫入時的一致性。**~~ **已量出答案(P8 演練)**:時間窗的上界是 run 開始時
  取的 `now`,所以備份進行中寫入的資料一律落在窗外,由下一趟增量接手。本機演練 100/100 的
  before 全數還原、after 0/5、during 0/8 —— 語意是可預期的,不是碰運氣。`scripts/backup_drill.py`
  每次都會把這三個數字印出來,所以在 stg 或 prod 上是多少,跑一次就知道。
- **目的地實體。** 先做 dev server(確定有),S3 是改一個 backend URL。
  容量抓 live size 的 1.5–2 倍。
- **加密金鑰必須存在叢集之外**,否則叢集沒了備份也打不開。這是 runbook 的一行,不是設計選項。
- **NAS 本身沒有 snapshot / replication**(user 確認),所以那棵樹也得我們自己來,
  範圍不能縮。
