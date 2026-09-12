# 檔案樹要懶載入 — 打開 item 不該等 50 秒

回報:`GET /a/{slug}/items/{id}/tree` 等 **50 秒、回 971 KB**。那是一萬兩千多個 entry。

問題不是「樹太大」,是**樹裡有一大塊機器產生的東西**(`node_modules/`、`.venv/`、`.git/`)。
使用者自己的檔案扣掉那些通常只剩幾百個,而那個量預載整棵是便宜的。所以答案不是全懶也不是
全預載,是**混合**:預載修剪過的整棵,修剪掉的目錄展開才讀。

**修剪 ≠ 隱藏。** 這是 user 親口鎖的:「只是預載入不是全載,但打開還是看得到。」

---

## 1. 現狀(已查證,基準 `d844a651`)

### 1.1 一次 `GET /tree` 做了什麼

| 步 | 位置 | 做什麼 |
|---|---|---|
| route | `api/file_routes.py:347` | `files.tree(investigation_id, prefix)` |
| facade(暖) | `files/facade.py:1159` | `sb.walk(h, prefix or "/")` |
| sandbox-host | `sandbox-host/src/sandbox_host/local_process.py:1318` | `base.rglob("*")` + 每個 entry `is_dir()` + `is_file()` + `stat()` |
| facade(冷) | `files/facade.py:1161-1163` | `_stat_all_cold` + `_fs.listdir` |
| nfs_tree(冷) | `filestore/nfs_tree.py:166-175`、`:180-185` | **兩次**整棵 `rglob`,走完**才**用 `prefix` 過濾 |

**實測**(`walk_one.py` / `probe_walk.py`,合成的 node_modules 形狀樹,11,700 檔 + 1,201 目錄):
出貨的 `_walk_sync` 對 12,901 個 entry 發了 **36,301 次 `os.stat`,每個 entry 2.81 次**。
NFS 上每一次都是一趟往返;50 秒 ÷ 36k ≈ 1.4 ms/趟,數字對得上。

⚠️ 探針盲點:計數器掛在 Python 的 `os.stat`,看不見 C 層的 `DirEntry.stat()`。出貨版走的是
pathlib,每一步都經過 Python,所以 2.81 是真的;`os.scandir` 版的省多少只能推論,不能宣稱實測。

`prefix` 今天在冷路徑**一毛都省不到**:暖路徑 `walk(h, prefix)` 從該子樹 rglob(這個省),但冷路徑
(`nfs_tree.py:175`)是走完整棵再 `startswith` 過濾。

### 1.2 誰在付這 50 秒、多常

| 呼叫 | 為什麼走整棵 | 位置 |
|---|---|---|
| `useFiles` → `api.getTree` | 畫樹 | `web/src/hooks/useInvestigation.ts:20-29` |
| `useRefreshFiles`:**每輪 turn 結束、每次 terminal exec 結束** | invalidate `qk.files` → 整棵重抓 | `hooks/useRefreshFiles.ts:24,45` |
| `file_changed` SSE | invalidate `qk.files` → 整棵重抓 | `hooks/useChatSession.tsx:329-332` |
| `writeVerified`:存檔的回應被切斷(0/502/503/504)時,列整個 workspace 確認那個檔案在不在 | #493;**只在不確定時**,不是每次存檔——本計畫初稿寫成「每一次存檔」,查證後改正 | `api/writeVerified.ts:42-53`、`api/fileService.ts:104-108` |
| `attachmentLanded`:附件上傳被切斷時,同上 | 同上 | `pages/investigation/AgentPanel.tsx:454-457` |
| `CardDiffReview`:**每次掛載**列整個 workspace 看一個固定路徑在不在 | 這條是真的每次 | `components/CardDiffReview.tsx:55-58` |
| `registry._is_cold`:`walk(probe, "/")` 只為問「目錄存在嗎」 | 只有 `kind: local`;http 走 `_alive` = `exists` | `api/registry.py:531-543`、`:368-378` |

所以那 50 秒**不是付一次**:開頁一次、每輪 turn 一次、每個 review 按鈕掛載一次、連線斷掉的那次存檔/附件再一次。
懶載入對 turn 結束的收益比對開頁還大。

**不是這包的**(它們走整棵有自己的理由,在自己的 sweep 上跑,不落在使用者的請求上):
mirror(`sync/sandbox_sync.py:150,182`)、`disk_usage`(`sandbox/docker.py:221`)、額度量測。

### 1.3 那一萬多個 entry 是什麼

機器產生的目錄。mirror **已經**拒絕保存它們 —— `sync/ignore.py:34` 的 `DEFAULT_IGNORES`
(`.venv/ node_modules/ __pycache__/ .git/ .pytest_cache/ .ruff_cache/ *.pyc *.pyo`)是一份審過的
「derived、絕不是 agent 自己的資料」定義。但樹照走、照畫、照傳。

⚠️ `DEFAULT_IGNORES` 有**第二個消費者**(`ignore.py:7-17`):schedule index 用它決定「平台不備份的檔
案就不從它接指令」。往裡面加一個 pattern 是**排程的產品決策**,不只是硬碟的。

### 1.4 前端哪些地方吃整份清單(為什麼不能全懶)

| 位置 | 假設 |
|---|---|
| `pages/investigation/fileTree.ts:84-95` `pruneTree`(#402 篩選) | 整棵在手 |
| `pages/investigation/WorkspaceShell.tsx:238` `surfaceTabs` 過濾 | **不在清單 = 不存在** |
| `WorkspaceShell.tsx:501-511` 清單裡沒有的 tab **自動關掉**(除非 dirty) | 同上 |
| `WorkspaceShell.tsx:1684` 最近檔案過濾 | 同上 |
| `WorkspaceShell.tsx:2346` 麵包屑的同層路徑 | 同上 |

全部建立在「清單是完整的」。純懶載入會讓它們**靜默地**錯:打開 `node_modules/x/y.js` 的 tab
在下一次 refetch 時自己關掉、篩選找不到明明在的檔案。這就是 Q4 那句「這是個問題耶」。

### 1.5 協定面

| 事實 | 位置 |
|---|---|
| sandbox 協定只有遞迴 `walk(handle, root)`、點查 `exists`/`size_of`;**沒有一層列表** | `sandbox/protocol.py:355` |
| `WalkResult(files, dirs)`;`dirs` 那半是 #678 為空資料夾加的 | `sandbox/protocol.py:187`、host `protocol.py:102` |
| `walk` 的呼叫者 8 處,全部 `walk(h, "/")` 或 `walk(h, prefix)` | mirror ×2、registry ×1、docker ×1、facade ×5(`facade.py:1038,1096,1135,1159,1170`) |
| 四份實作 + host 三處 | `sandbox/{local_process:1303, docker:284, http_client:682, mock:211}`;host `{local_process:1312, mock:153, app:693}` |
| `sandbox_host` 是獨立套件,`workspace_app` 不 import 它;`local_process.py` 本來就是**兩份拷貝** | CLAUDE.md #393 段 |
| rglob 在 3.12 **不進**指向目錄的 symlink,但 `p.is_dir()` 會跟,所以 symlink 目錄今天是「列出、空的、不進去」 | 同機實測 |
| 前端 `FileService.listTree()`;KB 的 adapter 是平的、沒有 dirs | `api/fileService.ts:47`、`api/kbFileService.ts:92` |
| 樹的狀態:`usePersistentSet("rca:tree-collapsed:…")`,**收起集合、預設全展開** | `FileTree.tsx:159`、`:953` |
| facade 有 `exists()` 點查(暖 → `sb.exists`,冷 → `fs.exists`),**沒有 HTTP 路由** | `facade.py:637-643` |

### 1.6 正式環境的「冷」不是 §1.1 那條冷路徑

正式環境是 `sandbox.kind: http` + `host_managed_durable`。這個拓撲下:

| 事實 | 位置 |
|---|---|
| item 的 sandbox address **只在刪 item 時清掉**;閒置 reap、關閉環境都**不清** | `api/item_routes.py:889`;`registry.py:835-836` 明寫「never cleared here」 |
| 所以閒置後重開:`_warm` 探到 `SandboxNotFound` → **rebuild**,不是走 nfs_tree 冷路徑 | `files/facade.py:330-336` |
| rebuild = host `create` 內**同步** rsync restore 整個歸檔,回來才 `mark_ready` | `sandbox-host/app.py:343-350` |
| 歸檔的 rsync **沒有任何排除清單**(`-rlptD`,無 `--exclude`);app-side 的 `DEFAULT_IGNORES` mirror 在 host-managed 下**根本不跑** | `nfs_archive.py:43`;`registry.py:160-163` |
| 因此 `node_modules/`、`.venv/`、`.git/` 全在歸檔裡,restore 會**整批搬回來** | 同上 |
| §1.1 的 nfs_tree 冷路徑只在**沒有 address** 的 item 走到:從沒跑過 turn 的、或 #366 之前建的 | `registry.py:199-204` |

結論:**一個有 `node_modules/` 的 item(有跑過 npm install ⇒ 有 address),閒置後第一次打開付的是
「rsync restore 一萬兩千個檔 + 暖路徑 walk 一萬兩千個 entry」兩段**,本計畫只修第二段。
nfs_tree 冷路徑仍然要修(user 點名、而且它今天是假 prefix),但它不是那 50 秒的主場。

---

## 2. 鎖定的決策

| 決策 | 內容 |
|---|---|
| **混合:預載修剪過的整棵** | 一次 walk,走到修剪清單裡的目錄**記下它存在、不進去**。修剪掉的目錄在樹上是**收起的節點、有 chevron**;展開 → `GET /tree?prefix=/node_modules&depth=1` 讀那一層,它的子目錄再懶 |
| **修剪 ≠ 隱藏** | 修剪只決定「不預載」。保存規則**一個字不動**(app-side mirror 照 `DEFAULT_IGNORES` 不存 `node_modules/`;正式環境 host-managed 的 rsync 歸檔沒有排除清單、全部都存 —— §1.6)、額度照算 bytes(#538 那條「樹顯示的就要算」不變)、展開看得到、agent `show_file` / 直接給路徑打得開。⌘P 是對預載清單搜尋,**找不到**懶目錄裡的檔(跟 §6.5 篩選同類,預期行為) |
| **修剪清單 = `DEFAULT_IGNORES` 裡的目錄 pattern + `dist/` + `build/`** | user:「先把常用的放進去」。一個**新常數** `TREE_PRUNE`,從 `DEFAULT_IGNORES` **導出**(同一個變數,不是抄一份數字),再加兩個。**不改 `DEFAULT_IGNORES` 本身** —— 它有兩個消費者(§1.3),加 `dist/` 進去會停止備份 `dist/` 並把裡面的排程靜默關掉。「不預載」是第三種語意,前兩種不動 |
| **只有目錄能修剪** | `*.pyc` 這種檔案 pattern 不進 `TREE_PRUNE`:檔案沒有「收起」可言,不列就是隱藏 |
| **沒有新的 sandbox op** | `walk(handle, root, *, depth=None, prune=(), max_entries=None)`,三個可選參數,8 個既有呼叫者一行不改。`WalkResult` 多兩個欄位:`unwalked: list[str]`(列出了但沒進去的目錄)、`truncated: bool`。API 端 `GET /tree?prefix=&depth=`;修剪清單和上界是**伺服端政策**,前端只選 depth |
| **結構性上界 `TREE_MAX_ENTRIES = 5000`** | 就算剪掉那些,萬一使用者真有五萬個 CSV,walk 要停。BFS 逐目錄走、**在目錄之間**檢查總數;超過就停,queue 裡還沒列的目錄全部進 `unwalked`,`truncated = true`。前端把它們當懶節點,篩選列標「部分資料夾未載入」。常數,不是 config 旋鈕(§5) |
| **一個機制,三個理由** | `unwalked` 的成員來自三個原因:在修剪清單裡、超過 `depth`、超過上界。前端不區分,全都是「展開才讀」 |
| **展開狀態:一條規則、兩個集合** | 預設:**走過的目錄展開(跟今天一樣)、`unwalked` 的收起**。走過的沿用 `rca:tree-collapsed:*`(在集合裡 = 收起,一個位元都沒變);懶目錄用新的 `rca:tree-opened:*`(在集合裡 = 使用者打開過)。初稿想共用一個集合、語意「翻過預設」—— review 抓到那會把**使用者部署前親手收起的 `node_modules/`**(等最久的那批人)讀成「打開過」,部署後第一次進來就自動展開並抓取。兩個集合各自的預設就各自誠實 |
| **不自動展開、不 reveal** | Q3。agent 寫檔不展開任何東西;開檔案也不展開它的祖先(今天也不會)。tab 存在的問題用下一條解,不用展開解 |
| **「不在清單」不再等於「不存在」** | 一個 helper `presenceOf(path, files, unwalked) → "present" \| "absent" \| "unknown"`,`unknown` iff 某個祖先在 `unwalked`。§1.4 那四處**只在 `absent` 時**才關 tab / 剔除。判準只裝在一個地方 |
| **失效:turn 結束、`file_changed` → 預載樹 + 已展開的懶目錄** | `qk.files(id)` 照舊 invalidate(現在只有幾百個 entry,便宜);另加 `["treeDir", id]` 前綴 invalidate —— TanStack 只重抓**還掛在畫面上的**(= 展開中的),收起的下次展開才讀。兩個入口共用一個 `invalidateTree(qc, id)` |
| **篩選** | 跑在「預載樹 + 已載入的懶目錄」上,語意跟今天一樣完整 —— 對使用者自己的檔案而言。`node_modules/` 不在篩選範圍是**預期行為**(每個 IDE 都這樣),不提示;只有 `truncated` 才提示 |
| **同一個病因一起掃:「列整個 workspace 只為確認一個路徑」** | 開一個 `GET /files/exists?path=` 路由接到既有的 `facade.exists`;`FileService.exists(path)`;`writeVerified`、`attachmentLanded`、`CardDiffReview` 三處改用;後端 `_is_cold` 改成跟 `_alive` 同一個探針(`exists(probe, "/")`)。**這條可以拆掉單獨做**,但它是同一個病因,寫下來免得各自另開一票。誠實的份量:`writeVerified`/附件只在連線斷掉時才列(少見),`CardDiffReview` 每次掛載都列(常見),`_is_cold` 只有 `kind: local` |

### 為什麼不是「把 rglob 換成 scandir 就好」

user 跳過了那個方案(「跳過 3 倍方案」),理由成立:2.81 → ~1 次/entry 是把**沒有上界**的東西
除以三,12k 個 entry 仍然是 12k 趟往返、仍然每輪 turn 付一次。上界才是把「凍住」換成「有答案」的東西。
scandir 在這包裡**順便**做,因為 BFS 走法本來就要逐目錄列。

### 為什麼修剪用名字不用大小

大小要先走完才知道;名字在進去之前就知道。而「什麼算機器產生的」這個問題已經有一份審過的答案
(`DEFAULT_IGNORES`),不需要再發明一個啟發式。

### 為什麼 `presenceOf` 要保守到「有 unwalked 祖先就 unknown」

更精確的版本是「該懶目錄已經載入過就能判 absent」。但那要把「載入過沒」帶進判準,而它會隨展開/收起
變動 —— 一個會隨 UI 狀態改變答案的「存在性」是 review 抓不到的那種錯。代價只有:`node_modules/`
底下被刪掉的檔案,它的 tab 要使用者自己關。接受。

---

## 3. 形狀

### 3.1 walk 演算法(一份,兩個拷貝)

```
walk_tree(list_dir, root, *, depth, prune, max_entries) -> WalkResult
  queue = [(root, 0)]; files = []; dirs = []; unwalked = []; n = 0
  while queue:
    d, lvl = queue.pop(0)
    if max_entries is not None and n >= max_entries:
        unwalked.append(d); continue            # 上界:列出過、沒進去
    for entry in list_dir(d):                   # 一次 scandir = 一個單位
        n += 1
        if entry is regular file: files.append(...)
        elif entry is dir (不跟 symlink):
            dirs.append(entry.path)
            if should_prune(entry.path) or (depth is not None and lvl + 1 >= depth):
                unwalked.append(entry.path)     # 修剪 / 深度:列出過、沒進去
            else:
                queue.append((entry.path, lvl + 1))
        elif entry is symlink to dir: dirs.append(entry.path)   # 今天的行為:列出、空的、不進去(§1.5)
        else: skip                              # socket / fifo / 斷掉的 symlink,跟今天一樣
  return WalkResult(files, dirs, unwalked=unwalked, truncated=(有目錄因上界進了 unwalked))
```

- `list_dir` 是注入的:host 與 nfs_tree 給 `os.scandir`(真的省往返);docker 給 `find -printf`
  的輸出、mock 給 in-memory dict、specstar 冷路徑給 `stat_all(prefix)` 的列(這三個省不到,但**答案
  一樣**)。一個演算法、幾個 adapter,不是每個實作各判一次。
- host 那份是 `sandbox_host` 套件裡的拷貝(§1.5 既有模式),**測試一起拷貝**。
- 上界在**目錄之間**檢查,所以超過量最多是一個目錄的大小。`depth=1` 時永遠不會截斷(只列一個目錄)。
  一個目錄裡有五萬個檔案 → 展開它回五萬筆。**已知,不在這包處理**(§5)。
- symlink 目錄:今天 rglob 不進、`is_dir()` 列出。保留:列在 `dirs`、**不**進 `unwalked` —— 進了
  `unwalked` 就會有人展開它,`depth=1` 從 link 走 scandir 會跟過去,而 link 可以指到 workspace 外面。
- symlink **檔案**:實作時查到今天的 `p.is_file()` 會跟 link,所以指向檔案的 symlink 是**列成檔案**
  (size 是目標的),不是「跳過」。保留(mirror 靠它);上面虛擬碼的「regular file」讀成「`is_file()` 為真」。
  P1 的回歸對照組(舊 rglob 走法當 oracle)就是為了抓這種細節。

### 3.2 API

```
GET /a/{slug}/items/{id}/tree?prefix=&depth=
→ { files: [{path,size,read_only}], dirs: [...], unwalked: [...], truncated: bool }
GET /a/{slug}/items/{id}/files/exists?path=
→ { exists: bool }
```

- `depth` 省略 = 預載(伺服端帶 `prune=TREE_PRUNE, max_entries=TREE_MAX_ENTRIES`);`depth=1` = 展開
  一層(伺服端同樣帶修剪與上界;`depth=1` 下修剪對子目錄無感,因為它們本來就 unwalked)。
- host 路由 `GET /sandboxes/{rid}/walk?root=&depth=&prune=&max_entries=`,回 body 多 `unwalked`、
  `truncated`。`http_client` 用 `body.get("unwalked") or []` 那條既有寫法讀 —— 跟 `dirs` 同一行的形狀,
  不是為版本歪斜設計的降級(host 與 API 同一條 CI/CD)。

### 3.3 前端

- `FilesState` 多 `unwalked: string[]`、`truncated: boolean`;`TreeNode` 多 `lazy: boolean`。
- `buildFileTree(files, dirs, unwalked)`:`unwalked` 裡的目錄建成 `lazy: true` 的節點,children 空。
- 懶節點展開 → `useQuery(qk.treeDir(id, path), () => svc.listTree({prefix: path, depth: 1}))`,
  結果 splice 進那個節點;它回來的 `unwalked` 再建成懶節點。收起 → query 失去 observer,不再重抓。
- `toggled` 集合(原 `collapsed`):`open = node.lazy ? toggled.has(p) : !toggled.has(p)`。
- `presenceOf` 在 `fileTree.ts`,四個消費者改用。
- `invalidateTree(qc, id)`:`qk.files(id)` + `["treeDir", id]`;`useRefreshFiles` 與 `file_changed` 都叫它。
- KB adapter:`unwalked: []`、`truncated: false`,其餘不動。

---

## 4. Phases

### Phase 1 — sandbox-host:`walk_tree` + `walk` 的三個參數

`sandbox-host/src/sandbox_host/{walk.py(新), local_process.py, mock.py, app.py, protocol.py}`。

**測試(先紅)**
- 修剪:`node_modules/x/y.js` 存在時,`walk(prune=["node_modules/"])` 的 `dirs` 含 `/node_modules`、
  `files` 不含任何 `/node_modules/…`、`unwalked == ["/node_modules"]`。**拿掉 prune 它必須紅。**
- 上界:3 個目錄各 10 個檔、`max_entries=15` → `truncated`、`unwalked` 正好是沒列到的目錄,而且
  `files ∪ unwalked 底下的東西 == 整棵`(一個都沒丟、一個都沒重複)。
- `depth=1`:只列一層,每個子目錄都在 `unwalked`;`truncated` 是 False。
- symlink 目錄:在 `dirs`、不在 `unwalked`、沒有它底下的檔案。
- **回歸對照組**:預設呼叫在 `probe_walk.py` 那棵樹上的 `files`/`dirs` 與舊 `_walk_sync` **逐項相等**
  —— 這條是 mirror 的守衛,mirror 只讀 `.files`。
- host 路由:`depth`/`prune`/`max_entries` 三個 query param 透傳,body 有 `unwalked`、`truncated`。

### Phase 2 — API 端協定、四份實作、facade、路由、修剪清單

- `sandbox/protocol.py:355` 簽名 + `WalkResult` 兩個欄位(預設空 / False)。
- `sandbox/walk.py`(P1 那份的拷貝,測試一起拷);`local_process.py:1303` 改用;`docker.py:284`、
  `mock.py:211` 給各自的 `list_dir` adapter;`http_client.py:682` 透傳 + 讀新欄位。
- `sync/ignore.py`:`TREE_PRUNE = [p for p in DEFAULT_IGNORES if p.endswith("/")] + ["dist/", "build/"]`,
  註解寫明它**不是** mirror 也**不是** schedule index 的清單;`TREE_MAX_ENTRIES = 5000`。
- `facade.tree(workspace_id, prefix, depth)`:暖 → 帶 `prune`/`max_entries`/`depth`;冷 → `nfs_tree`
  新增 duck-typed `tree(...)`(scandir 的 `walk_tree`),沒有的 store 走既有 `_stat_all_cold` + `listdir`
  再用 `walk_tree` 的 in-memory adapter 修剪。
- `file_routes.py:347` `depth: int | None = None`;`_WorkspaceTree` 多兩欄。

**測試(先紅)**
- 走**真路由**(TestClient + MockSandbox):`node_modules/` 底下有檔 → 預載回應的 `files` 不含它、
  `unwalked` 含它;`?prefix=/node_modules&depth=1` 回它那一層。
- 冷路徑同一組(`nfs_tree` 實體 tmp 目錄,不經 sandbox):**user 提醒「冷載入也可能需要考慮」**,
  這條就是那個守衛。結果相同不夠 —— 退回整棵 rglob 結果也相同 —— 要斷言的是 **`prefix` 之外沒被碰**,
  用 `os.scandir` 計數器(不是 `os.stat`,pathlib 的 rglob 也走 scandir,所以計數器要記**哪些目錄**被列)。
- `TREE_PRUNE` **只含目錄 pattern**,且是 `DEFAULT_IGNORES` 目錄部分的超集:往 `DEFAULT_IGNORES` 加一個
  目錄它跟著變(導出關係的守衛)。
- `DEFAULT_IGNORES` **一個字都沒變**:`tests/api/test_schedule_index.py` 既有那組照舊綠,不加東西。

### Phase 3 — 前端:懶節點、展開讀取、展開狀態

`api/types.ts:678`、`api/real.ts:558`、`api/mock.ts:988`、`api/fileService.ts:47`、`api/kbFileService.ts`、
`api/queryKeys.ts`(`treeDir`)、`hooks/useInvestigation.ts`、`pages/investigation/{fileTree.ts, FileTree.tsx}`。

**測試(先紅)**
- `buildFileTree` 把 `unwalked` 建成 `lazy` 節點、children 空。
- 懶節點初始收起、有 chevron;點開 → 對 `listTree({prefix, depth: 1})` **恰好一次**呼叫(fetch stub
  要先濾路由);回來的子目錄又是懶節點。
- 走過的目錄**預設展開**(既有測試不動 —— 它們就是回歸對照組);unwalked 目錄預設收起;`toggled` 對兩種
  節點各自翻轉;**既有 `rca:tree-collapsed:*` 的資料**照舊讓走過的目錄收起。
- KB adapter 的樹一個節點都不是 lazy。

### Phase 4 — 前端:`presenceOf` 與四個消費者

`fileTree.ts`(helper)、`WorkspaceShell.tsx:238,501-511,1684,2346`。

**測試(先紅,兩個方向都要)**
- 開著 `/node_modules/x/y.js` 的 tab,預載樹 refetch(清單裡沒有它、`unwalked` 有 `/node_modules`)→
  **tab 還在**。
- 開著 `/src/a.py` 的 tab,refetch 後清單裡沒有它、`unwalked` 空 → **tab 關掉**(正向對照組:規則沒壞)。
- dirty 的 tab 在兩種情況都不關(既有行為)。
- 最近檔案、麵包屑同層、`surfaceTabs` 各一條 `unknown` 不剔除的測試。

### Phase 5 — 前端:失效與篩選提示

`hooks/useRefreshFiles.ts`、`hooks/useChatSession.tsx:329-337`、`FileTree.tsx`(#402 標頭)、`lib/i18n.tsx`。

**測試(先紅)**
- turn 結束:展開中的懶目錄被重抓、收起的**沒有**(數 `listTree` 呼叫,先濾路由)。
- `file_changed` 同上。
- `truncated` 時篩選標頭顯示提示;不 truncated、只有 pruned 目錄時**不**顯示。

### Phase 6 — 同病因掃除:`exists`

`api/file_routes.py`(新路由)、`api/types.ts`、`api/real.ts`、`api/mock.ts`、`api/fileService.ts:104-108`、
`pages/investigation/AgentPanel.tsx:454`、`components/CardDiffReview.tsx:55`、`api/registry.py:531-543`。

**測試(先紅)**
- 路由:暖走 `sb.exists`、冷走 `fs.exists`(facade 既有,只加路由測試)。
- `writeVerified` 成功路徑**不再**呼叫 `listFiles`(數次數、濾路由);#493 那條「回應斷了但檔在」的既有
  測試照舊綠。
- `_is_cold`:目錄不在 → True、在 → False,**且不呼叫 `walk`**(mock 計數);`kind: local` 的 `_workspace`
  對不存在的目錄丟 `SandboxNotFound` 這件事要先驗過再依賴 —— `_alive` 已經靠它,但要有測試釘住。

### Phase 7 — 文件

CLAUDE.md 架構段加一條「檔案樹是預載修剪樹 + 懶目錄」,把 `TREE_PRUNE` 與 `DEFAULT_IGNORES` 的三種
語意(不保存 / 不接指令 / 不預載)寫在同一處;`sync/ignore.py` 頂部那段警告加上第三個消費者。

---

## 5. 非目標

- **不動 mirror 的 walk。** `registry.flush` / `SandboxSync` 每次 sweep 走整棵是耐久性的事,而且它
  **必須**看到 `dist/`(它要保存)。它拿到的 `WalkResult` 跟今天一樣(預設參數)。
- **不動額度。** #538 那條「樹顯示的就要算」不變;`node_modules/` 照算。
- **不動歸檔的 rsync(user 鎖定)。** restore 一個檔都不省 —— 排掉 derived 目錄會讓 reap 後的環境壞掉。§6.8。
- **不改 `DEFAULT_IGNORES`。** §1.3。
- **不做 reveal / 自動展開。** Q3。
- **不做伺服端篩選 / 檔名索引。** `POST /search` 存在,篩選要不要接它是獨立決定;這包裡篩選語意
  對使用者自己的檔案已經完整。
- **不把上界做成 config 旋鈕。** 常數。做成旋鈕要記 migrations 帳、要 example yaml,而它今天沒有
  任何一個部署需要不同的值。真需要時再升。
- **不處理「一個目錄五萬個檔案」。** 上界在目錄之間,單一目錄不截。要處理是 per-dir 的分頁,另一包。
- **不動 `isolated_process._reown_sync` 的 rglob**、不動 `nfs_tree` 給**別人**用的 `stat_all`/`listdir`
  (`GET /files` 路由 `file_routes.py:324` 仍是整棵;P6 之後前端不再有人為了一個路徑呼叫它)。

---

## 6. 風險

### 6.1 最會靜默出錯的地方是 `presenceOf`

錯的形狀:tab 自己關掉、最近檔案少一筆、麵包屑少一個同層 —— 沒有錯誤訊息。P4 兩個方向的測試就是
為這個;缺任何一個方向都不算守住。

### 6.2 rglob → scandir 的行為差

三處要一樣:regular file 才進 `files`、symlink 目錄列出不進、其它跳過。P1 的逐項相等對照組守
`files`/`dirs`;symlink 那條單獨釘。**不能**只斷言數量相等。

### 6.3 前端「預設展開」

我最早的 Q1 草稿(只讀 root 一層)寫的是「集合翻成 `expanded`、預設全收」。那是**全懶**模型的需要;
混合模型下走過的部分很小,今天的預設展開保得住,不該為了懶節點順手改掉所有人的預設。
本計畫鎖的是「走過的展開、unwalked 的收起」—— 這是對 Q1 草稿的**修正**,寫在這裡讓它可被否決。

### 6.4 `depth=1` 的展開回應沒有上界

§3.1 已知。一個目錄五萬個檔案 → 一次回五萬筆。今天同一個目錄是回在 971 KB 裡,所以不是回歸,
但也不是修好。

### 6.5 篩選看不到 `node_modules/`

預期行為,但**是**行為改變:今天篩 `lodash` 找得到 `node_modules/lodash/`,以後找不到(除非展開過)。
不提示(§2)。如果有人真的靠這個,再談。

### 6.6 review 第一輪抓到的、計畫沒想到的

- **「存不存在」的問題不能只問預載清單**:`ensureReplaceable`、上傳撞名、Enter 開檔、「New file」落點原本都讀 `files`/`dirs` props,
  懶目錄載入後的檔案不在裡面 → 在 `dist/` 裡新建 `index.html` **不會提示、直接清空**(HIGH)。修法:合併後的 `listing` 只算一次,
  所有判斷都讀它。教訓:「清單」在這包裡有兩份,每個讀清單的地方都要問「哪一份」。
- **scandir 的錯誤要逐 entry 接**:舊 rglob 靠 `Path.is_dir()` 吞掉 ELOOP/EACCES;新的 `DirEntry` 會丟。一個循環 symlink 讓整棵樹 500、
  `kind: local` 的 mirror 停擺;一個 entry 在 readdir 與 stat 之間被刪,若在目錄層接錯誤,整個目錄回空 → mirror 刪掉所有同層的耐久副本。
- symlink **root**:`?prefix=/link-to-outside&depth=1` 會跟出 workspace(舊 rglob 對 symlink base 也一樣;`read` 也從不檢查)。既有一類,沒在這包修,記著。

第二輪(源自第一輪修法的發現:**0 條**;但第一輪那條 HIGH 修得不完整):
- **懶目錄還沒載入時**(收起、剛點開還沒回來),合併清單本來就不可能知道裡面有什麼 —— 右鍵「上傳到這裡」、拖放、剛開就打字,
  仍然無提示覆寫。判準不能只看清單:路徑在懶目錄底下就問伺服器 `exists`(P6 開的那條路,「一個路徑一個問題」)。
- 右鍵「New file…」對收起的目錄沒有 `ensureOpen`,輸入框畫在目錄裡面 → 什麼都沒出現(master 對收起的走過目錄也如此,只是走過的預設展開很少碰到)。
- `URLSearchParams.size` 舊瀏覽器(Chrome<113/Safari<17/Firefox<115)沒有 → query 被丟掉 → 每次展開抓到整棵預載。改 `toString()`。
- `usePersistentSet` 換 key 時把**舊 key 的狀態存進新 key**(hook 本來就有的缺陷);有了「已開啟的懶目錄」集合後症狀變成 B item 的 `node_modules` 自動展開抓取。
  修在 hook(state 記住它是從哪個 key 載的),deque 同類一起修。
- `flat_lister` 每個目錄掃整份清單(O(dirs×entries)):12k 檔 1.1 秒、50k 檔 15 秒,在冷 specstar 路徑落在請求上。改成建一次索引。
- 展開中的懶目錄沒有載入指示,跟「空的」分不出來。

第三輪(只看第二輪的修法;源自它的發現:**2 條**):
- `flat_lister` 的索引假設 key 是正規路徑:in-memory sandbox 存的是原字串(`pyproject.toml` 沒有前導 `/`、agent 可能寫 `//x`),
  舊的逐目錄掃描會默默略過,索引則 `KeyError`;`//` 目錄會變成名為 `""` 的子節點、路徑又是 root → **無限迴圈**。
  既有測試 `test_workflow_run_node_env.py` 因此紅。修:進索引前正規化、用原 key 查 entry、空名跳過。
- upload 迴圈裡 `await pathExists` 在 try 外面:`exists` 失敗(502/斷線)→ 整批上傳靜默中止、什麼都沒顯示。修:接住、記進 `problems`、繼續下一個;
  失敗**不等於**「不存在」。
- 已知未修(LOW):`exists` 只答檔案,懶目錄底下**資料夾形狀**的撞名(新檔取了既有子資料夾的名字)沒有 Replace 提示 ——
  搬移/改名走後端 409 會 alert,新檔在暖路徑 500、冷路徑在旁邊建同名檔。要修得開 `is_dir` 路由或讓 `exists` 回 kind;記著。
- 懶目錄抓取失敗時畫「載入失敗」,不再看起來像空的(P3 起就有的洞,順手補)。

第四輪(只看第三輪的修法;源自它的發現:**1 條 LOW + 1 條文案**,都只影響測試替身或措辭,修完收斂):
- `flat_lister` 正規化後,in-memory `MockSandbox` 的 `exists/download/delete` 仍用原 key 比對 → walk 報 `/pyproject.toml`、mock 卻查不到。
  只有測試替身會這樣(scandir / find / 耐久列都給正規 key);修在 mock:查不到原樣就以正規化等價比對。兩份 mock 一起。
- 「載入失敗」只在**沒資料**時標:已載入的層背景重抓失敗,列還在,底下寫「載入失敗」是假話。

**收斂梯度:6 → 0(P8)/ 殘留 6 → 2(P9)→ 1 LOW(P10)。** 判準是「幾條源自上輪修法」,不是「幾條」;
第四輪那條連使用者都碰不到,停。

### 6.7 冷路徑 `prefix` 從「假的」變「真的」

`nfs_tree` 的 `tree()` 從 `prefix` 開始 scandir,所以 `?prefix=/node_modules&depth=1` 在冷 item 上
真的只碰那一層。這是修正不是風險 —— 但它同時意味著**冷暖兩條路第一次有相同的成本形狀**,
P2 的冷路徑測試要記「哪些目錄被列」,不是只看結果。

### 6.8 做完之後,閒置後重開**還是會等** —— restore 那一段不在這包

§1.6:正式環境閒置後第一次打開 = rsync restore 整個歸檔 + walk。本計畫把 walk 從「一萬兩千個 entry
× 2.81 趟」壓到「幾百個 entry × ~1 趟」,但 restore 仍然搬一萬兩千個檔。**那 50 秒有多少是 restore、
多少是 walk,從這裡量不出來**(NFS 延遲本機重現不了;要在 host 上對一個真歸檔跑一次 restore 計時)。

唯一能讓 restore 變快的方法是給歸檔的 rsync 加排除清單 —— **user 鎖定:不做。** 排掉 `node_modules/`
等於「閒置 reap 之後裝好的套件不會回來」,環境直接壞掉,比慢還糟。restore **一個檔都不省**,
這不是「先量再談」,是定案。所以閒置後重開的等待時間有一段是**刻意保留**的;之後有人回報「第一次打開
還是慢」,先分清楚是 restore 還是 walk,別回頭動歸檔。

同一個發現順帶指出一件事:`/my-resources` 執行環境區那句「裝好的套件與版本紀錄不會保留」是照
app-side mirror 寫的;host-managed 拓撲下 workspace 內的 `node_modules/`、`.venv/` **會**跟著歸檔回來
(只有 `.home` 裡 pip `--user` 裝的不會)。那句話在正式環境上是半錯的 —— 另開一票,不在這包。
