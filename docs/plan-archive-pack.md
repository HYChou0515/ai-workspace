# Plan: a packed copy of the archive for the reopen path (`<item>.pack.<gen>-<bytes>.tar`)

## 問題（2026-10-06，master `2364e23d`）

user 回報：一個 item 的沙盒被回收之後再開，「開不起來」。查下來是**慢**，不是壞：

| 事實 | 數字 | 來源 |
|---|---|---|
| workspace 路徑數 | **88,889**（51,911 個檔），**幾乎全是使用者檔案**，不是 `node_modules` | user |
| host 從 NFS rsync 進 pod | **58 秒** | user 在 host pod 實測 |
| `reown`（每路徑一次 `os.chown` + `rglob`） | 約 1 秒 | 本機量 44,310 路徑 0.49 秒，線性外推 |
| `POST /sandboxes` 全程 | 約 60 秒 | 上兩列相加 |
| ingress-nginx `proxy-read-timeout` 預設 | 60 秒 | 文件；**線上實值我看不到** |
| host idle reaper `SANDBOX_HOST_IDLE_TTL` | 1800 秒 | `config.py:48`；碰不到 |

所以是**剛好壓在線上**：再多幾千個檔或 NFS 慢一點就 504。而 504 會自己惡化——`.ready` 是 restore 做完才寫，
位址也是那時才發佈；使用者重新整理、請求落到另一顆 API pod，它看不到位址就**再開一次完整的 58 秒 restore**。
跨 pod 沒有互斥，4–16 顆可以疊成好幾份同時 rsync 同一個備份。

**成本模型**：58 秒 ÷ 88,889 ≈ **0.65 ms／路徑**，是 NFS 一次往返的量級；若是位元組主導，58 秒要搬好幾 GB。
所以瓶頸是**路徑數**，而路徑數沒有任何上限（`workspace_quota` 管位元組；CLAUDE.md 明文拒絕第二個尺寸上限）。
任何「複製」的還原都是 O(路徑)；要讓 NFS 那側變成 O(1) 往返，只能讓備份有一個**單一檔**的形態。

## 走過的死路（記下來，免得下次再走）

| 想法 | 為什麼不 |
|---|---|
| rsync 換 tar／cp 直接複製樹 | 還是每檔一次 NFS 往返；本機 bench 的倍數（2.6×／1.75×）不能外推到 NFS；不封頂 |
| 在每個對齊點（含 **turn 結束**）打包 | 打包 ∝ 位元組、位元組未知，放在有人等的路徑上 |
| 慢速還原完順手在背景打包 | 那時沙盒已經活了，tar 讀到一半 agent 在寫 → 一半新一半舊；`.gen` 只看得到樹的寫入，看不到本機 |
| 回收時不刪本機目錄、下次重用 | 每顆 pod 自己的、rollout 就消失的快取，而且要把 create 導回那顆 pod＝把 #345/#366 拆掉的 affinity 裝回去 |
| 備份排除 `node_modules` 等可重建目錄 | 違反 SRP（備份的契約「原樣回來」≠ app 端 `DEFAULT_IGNORES` 的「使用者檔案有哪些」）；而且這次檔案全是使用者的，排了也沒用 |
| overlay（NFS 當唯讀底層）／沙盒直接跑在共用卷上 | scalable 且保住契約，但要 `CAP_SYS_ADMIN`、lower 在掛載中被改是核心層未定義行為、root_squash 下的讀取要驗——是專案不是票；留作下一步 |
| pack 先發佈再驗 `.gen` | host 死在「發佈後、驗之前」留下舊 pack；改成**名字帶 gen**後這個窗口不存在 |
| 在回收的 `persist` 請求裡打包，用「in-flight 計數 + 沒有人在我之後開始」當 guard（P2–P6 的做法，第一輪 review 推翻） | app 的 `mirror_warm` 每 5 秒對**每一個**有 handle 的 session 送 checkpoint——閒置 8 小時的也送，每顆 pod 各送各的。打包要的時間和位元組成正比，大 item 的 rsync + tar 遠超過 5 秒，所以 guard 幾乎必定被 checkpoint 打斷：功能要服務的 item 恰好永遠拿不到打包檔。guard 也補了兩次洞（`t0` 讀得太晚、時鐘值可能相同）——第三次就換機制 |

## 決定

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 真相是誰 | **樹**（`<nfs_root>/<item>/`）永遠是真相；pack 是加速用的快取。任何失敗的結果都是「沒有 pack → 走樹」，**只會慢，不會錯** | #492 的設計不變；`_would_wipe`、`.ready` gate、`reown` 順序全不動 |
| 2 | pack 怎麼知道自己還有效 | **名字帶 gen 和位元組數**：`<item>.pack.<gen>-<bytes>.tar`。`<item>.gen` 是一個 uuid，每個要寫樹的人**寫之前先換掉**（tmp + rename）。還原讀現在的 gen、只找那個名字；舊 pack 因為名字過期而自然作廢，**不需要刪、不需要事後驗**。還原時檔案大小 ≠ 名字上的位元組數 → 當作沒有 pack | 消掉「刪 pack 和 rename 誰先」與「發佈了來不及驗」兩個窗口；NFS 沒有鎖，靠不可變的名字比靠順序可靠。位元組數是 P2 才加的：**GNU tar 對剛好在檔案邊界被截斷的 archive 回 0**——解出前半、當成結尾——所以「tar 成功」不等於「workspace 完整」（`test_a_truncated_pack_is_never_extracted` 先紅過） |
| 3 | 誰、何時打包 | **只有 `kill_idle` 的回收**要求打包：它的 `persist(delete=True, pack=True)` 在 rsync 對齊**真的跑了**之後，讓 host 記下這個沙盒「砍的時候要打包」（`_would_wipe` 把對齊降成非刪除的複製時不記——那時本機是空的，打包等於把空 workspace 發佈成快路）。**打包發生在接著的 `DELETE /sandboxes/{rid}` 裡**，沙盒拆掉之前。`flush`（turn 結束）、`close_all`（關機）、`_teardown`（關閉／CAS 輸家砍孤兒）、mid-turn checkpoint 一律 `pack=False`；之後的 checkpoint 不會取消這個要求（P8） | 回收是「全域閒置、沙盒要拆掉、沒人等」唯一同時成立的時刻；關機有預算而打包 ∝ 位元組；孤兒的本機目錄不是真相。放在 kill 而不是 persist：見死路表最後一列 |
| 4 | 本機那側怎麼知道沒人在寫 | kill **先封鎖**這個 rid：純 ASGI middleware（`_InFlight`）對封鎖中的 rid 一律直接回 `SandboxNotFound`，任何 pod 的 checkpoint、檔案寫入、exec 都進不來。封鎖之前就開始、還在跑的請求由同一個 middleware 的 **per-sandbox in-flight 計數**看見（進 +1、出 −1，含例外；必須是純 ASGI，因為 exec 是 streaming response，`@app.middleware` 的 `call_next` 在 response **開始**時就回來了）：計數 > 1 → 不打包。**不等**。kill 失敗 → 解除封鎖（沙盒還活著，要能重試） | 封鎖之後沒有新請求，所以只需要問一次「現在只有我嗎」，不需要時間戳或序號；被擋下的 checkpoint 得到的答案（沙盒不見了）正是它幾毫秒後會得到的。打包失敗只記 warning，不擋 kill：回收是沙盒消失，打包只是讓下次快 |
| 5 | tar 怎麼打、怎麼解 | `tar --format=posix -cf`；解 `tar -xf --no-same-owner`，之後照舊 `reown`、`mark_ready` | rsync 刻意不碰擁有者（NFS root_squash，#492 Q3），tar 用 root 解開預設會照檔頭設回去；posix 格式帶次秒 mtime |
| 6 | 解開壞掉怎麼辦 | 解進剛建好的（空的）本機目錄；非零結束 → 清空目錄 → 走樹。截斷的 pack 在解之前就被決定 2 的大小檢查擋掉 | 半個目錄絕不能被 `mark_ready`，否則下一次 `persist --delete` 把備份對齊成那半個 |
| 7 | 空間 | 閒置的 item 多一份 pack，最多約 2× | 可之後再加「路徑數超過門檻才打包」或壓縮；都不影響對錯 |
| 8 | 開關 | host env `SANDBOX_HOST_ARCHIVE_PACK`（預設 `1`）；`0` = 不打包也不讀 pack | 空間是營運方的事，要有退路；記進 migrations.md |
| 9 | 不經 persist 改樹的人 | **規則**：任何直接動 `<item>/` 的**人工**操作（#867 runbook 的 `find … -delete`、從備份還原 NFS 樹、手動 rsync）做完要刪掉 `<item>.pack.*.tar`。**app 自己的程式路徑查過，沒有一條會在打包檔存在時繞過 host 寫樹**（下表）。刪除 item 的 `purge` 原本會把打包檔留成孤兒，P5 補上 | 機制上關不掉：還原時驗 pack 等不等於樹就是 O(路徑)，等於沒做。這是整份設計**最弱**的一點，靠 runbook 不靠碼；漏做的方向**不是慢，是修改被撤銷**（還原出改之前的內容，turn 結束的對齊再把樹改回去） |
| 10 | 相容 | `_PersistBody.pack: bool = False`；舊 host 忽略多的欄位，舊 app 從不送 → rollout 順序自由。**但 host 回滾不自由**：舊 host 寫樹不換 `.gen`，回滾期間的寫入不會讓新 host 留下的打包檔作廢——回滾後、再往前滾之前要刪掉所有打包檔（migrations.md） | 和 `tools` / `cpu_cores` 欄位同一套慣例。回滾那條是第一輪 review 的 defect 鏡頭用 master 的 rsync argv 重現的 |
| 11 | 舊 pack／tmp 的清理 | 每次 persist 的第 1 步刪掉這個 item 的所有 pack 和殘留的 pack tmp。比對用 regex 全名比對，不是前綴——item id 是自由文字，`a.pack.x` 是別的 item 的東西。tmp 名字一律帶 uuid（`<item>.gen.<uuid>.tmp`、`<item>.pack.<uuid>.tmp`）：同一個 item 的兩個 persist 沒有任何東西在排序，共用的 tmp 名讓後一個 rename 找不到檔、persist 失敗（P7） | 懶清；host 死在寫到一半時留下的 tmp 由下一次 persist 或刪除 item 清掉 |

## 機制

檔案（全在 item 目錄**外面**，`persist --delete` 對齊那棵樹時碰不到）：
```
<nfs_root>/<item>/                          樹，真相
<nfs_root>/<item>.gen                       uuid；寫樹之前先換
<nfs_root>/<item>.gen.<uuid>.tmp            換 .gen 時的 tmp
<nfs_root>/<item>.pack.<gen>-<bytes>.tar    快取；只在「已回收」期間存在
<nfs_root>/<item>.pack.<uuid>.tmp           寫一半的，永遠不會被當成快取
```

回收：
```
app  kill_idle 判定全域閒置
 └─► host  POST /sandboxes/{rid}/persist {delete: true, pack: true}
        1. .gen ← g0 = uuid4()（寫 <item>.gen.<uuid>.tmp 再 rename）；刪掉這個 item 的 pack 與 pack tmp
        2. rsync 本機 → 樹，--delete（_would_wipe 擋下 → 到此為止，不記）
        3. 記下：rid 砍的時候要打包；回 204
     （這裡到第 4 步之間，任何 pod 的 checkpoint 照常寫樹、換 gen——不影響，打包時才讀 gen）
 └─► host  DELETE /sandboxes/{rid}
        4. 封鎖 rid：之後所有 /sandboxes/{rid}/… 請求直接回 SandboxNotFound
        5. 有記第 3 步、in-flight == 1（只有這個 DELETE）、沙盒 ready、開關開著 → 打包：
             g = 讀現在的 .gen（沒有 → 不打包）
             tar --format=posix 本機 → <item>.pack.<uuid>.tmp（讀本機；寫 NFS 一條串流）
             tar 非零、或 tmp 已被別人清掉 → 不發佈
             os.replace(tmp → <item>.pack.<g>-<tmp 的位元組數>.tar)
           任何例外 → warning，照樣往下
        6. 砍沙盒、刪本機目錄、清 .ready
        7. 解除封鎖、忘掉第 3 步的記錄（第 6 步失敗也一樣：沙盒還活著，要能回應、能重試）
```

其他寫樹的人（`delete: false` 的 checkpoint、`flush`、`close_all`、`_teardown`）：只做第 1–2 步。
一寫樹，現有 pack 的名字就過期；打包途中有人寫樹，打包讀的是寫之前的 gen，發佈出來就已經過期。

還原（`_Controller.create` 不變，只是 `NfsArchive.restore` 多一條分支）：
```
g = 讀 .gen；<item>.pack.<g>-<n>.tar 在、且大小 == n → tar -xf --no-same-owner 進新目錄 → reown → mark_ready
不在（或 .gen 不在、或大小不符）→ rsync 樹（今天的路）
解開失敗 → 清空目錄 → rsync 樹
```

開關 `SANDBOX_HOST_ARCHIVE_PACK=0`：第 5 步不打包、還原不找 pack（第 1 步照樣換 gen，所以之後再打開不會撿到舊 pack）。

### 決定 9 的依據：app 端直接寫樹的每一條路（P5 時逐條查）

host-managed 模式下 app 的 durable store 就是那棵樹（`NfsTreeFileStore`），所以「誰會不經 host 寫樹」要一條一條看：

| 寫入者 | 會不會在打包檔存在時寫樹 | 依據 |
|---|---|---|
| `WorkspaceFiles` 冷寫（`files/facade.py` 的 `self._fs.write/delete`） | 不會 | 冷寫只在 `resolve_io_handle` 回 None 時發生；http 的位址列在回收時**不清**（`kill_idle` 不呼叫 `address.forget`）、也沒有過期，所以回收過的 item 一律探到舊位址 → `SandboxNotFound` → `rebuild_io_handle` 經 host 建沙盒，寫進活的沙盒 |
| `seed_item` / `/collections.json`（`item_routes.py` 建 item） | 不會 | 只寫還沒有 row 的新 item；新 item 沒被回收過，沒有打包檔 |
| #492 M2 drain（`MigratingFileStore.backfill_workspace`，create 前） | 會寫，但結果是對的 | 它只補「舊 store 有、樹沒有」的檔；舊 store 凍結，而任何打包檔存在之前這個 item 的 drain 至少成功過一次（失敗就拒絕建沙盒），所以回收後還能補的只有使用者自己刪掉的檔——從打包檔還原時不帶回它們、turn 結束再從樹刪掉，比今天（把刪掉的檔復活）更對 |
| 刪除 item（`item_routes.py` 的 cascade → `filestore.purge`） | 刪樹，打包檔原本留著 | P5：`NfsTreeFileStore.purge` 一併刪掉 host 放在旁邊的 `.gen`／打包檔；測試用 host 真正的 `NfsArchive` 產生那些檔當 oracle |

## 還留著的洞

除了 1，失敗方向都是「慢」。

1. **人工不經 persist 改樹**（決定 9）——規則，不是碼；漏做的方向是**修改被撤銷**。
2. **3–4 之間（兩個請求之間）有人寫本機**：打包讀的是第 5 步當下的本機，所以 pack **比樹新**（含那筆寫入）；樹要到下次開、第一次寫回才跟上。方向是保住使用者最後的狀態——今天這筆寫入會被第 6 步砍掉。
3. **host 在第 5 步中途死掉**：`<item>.pack.<uuid>.tmp` 留在 NFS 到下次 persist（或刪除 item）。永遠不再被碰的 item 留一個死檔。
4. **封鎖期間的請求一律被當成沙盒不見了**：本來就要砍了，差別只有幾秒；app 對 `SandboxNotFound` 的反應（rebuild）和砍完之後一樣。
5. **兩個 host 上同一個 item 的沙盒（#366 CAS 輸家）**：另一個沙盒在我讀 gen **之前**寫了樹，pack 會是我這邊的本機——比樹少了它的寫入。這是 #366 split-brain 的同一類，今天它的寫入也會被我的對齊 `--delete` 蓋掉。
6. **成本，不是對錯**：每次 persist 的清理 glob 一次 `<nfs_root>`，而那個目錄每個 item 一筆、現在每個寫過的 item 還多一個 `.gen`。每顆 pod 每 5 秒對每個 warm 沙盒 checkpoint 一次，所以這是一個新的、每次 checkpoint 都付的目錄列舉。和 rsync 本身逐檔 stat 的成本比起來小；沒有量過，量到才改（例如「`.gen` 已存在且沒有打包檔」時跳過）。
7. **既有、沒變**：#366 CAS 輸家的陳舊 persist 覆蓋樹——pack 跟著樹走，不更好也不更壞；回收砍到正在用的沙盒那個 TOCTOU 也還在（in-flight 計數其實是比心跳更準的「有人在用」訊號，照理整個回收都該中止，但那是改回收的行為，另一題）。

## Phases

| phase | 內容 | 驗收（先紅後綠；突變各紅一條） |
|---|---|---|
| P1 | 這份 plan + design-history 一行 | `tests/docs/test_docs_index.py` 綠 |
| P2 | **host**：`NfsArchive` 加 `_new_generation` / `_pack_dir` / 清理 / `restore` 的 pack 分支；`_Controller.persist(rid, delete, pack)`；middleware 的 in-flight 計數；`_PersistBody.pack`；env `SANDBOX_HOST_ARCHIVE_PACK` | 紅：pack 名字帶 gen、還原只認現在 gen 的名字（舊名字在也不用）、寫樹換 gen 後既有 pack 不被用、有別的請求在跑就不打包、**截斷在檔案邊界的 pack 不被用**、tar 期間 in-flight／last_active 變了就刪 tmp、tmp 永遠不被用、解開失敗清目錄走樹、`delete=False` 永不打包、解開用 `--no-same-owner`、**pack 還原後 `rsync --dry-run -i` 重傳 0 個檔**（本機已驗兩種格式都 0）、開關 0 時不打包不讀。`Runner` 接縫照舊（tar 的 create／extract 都是單一 argv） |
| P3 | **app**：`HttpSandbox.persist(handle, *, delete, pack=False)`；`registry._writeback(..., pack=False)`；**只有 `kill_idle`** 傳 `pack=True`；`flush` / mid-turn checkpoint / `close_all` / `_teardown` 不傳 | 紅：`kill_idle` 的 persist 帶 `pack: true`；其餘四條帶 `false`（各一條；五個呼叫點逐一突變，各恰好紅一條）；HTTP body 兩個方向的突變紅 `test_persist_sends_pack_only_when_asked`。舊 host 不認 `pack`：master 的 `_PersistBody` 是沒設 `extra` 的 pydantic model，預設忽略多的欄位（用同一個 model 形狀驗過，不是測試） |
| P4 | **host 的可見性**：host 沒設 log level（root 預設 WARNING，`logger.info` 在 pod 上看不到），所以解開失敗、退回走樹印一條 **warning**（帶 item、pack 名、tar 的 stderr）；開機 echo 帶 `archive_pack=`。補兩條沒走到的分支：名字只是長得像（`<gen>-*.tar` 的 `*` 不是數字）、列出之後 stat 之前 pack 被刪 | warning 降成 info → 紅那一條；兩條分支各自的突變各紅一條 |
| P5 | **刪除 item 帶走打包檔**：`NfsTreeFileStore.purge` 一併刪 `<item>.gen`、`.gen.tmp`、`<item>.pack.<gen>-<bytes>.tar`、`.pack.tmp`（regex 全名比對，不是前綴）。測試從原始碼載入 host 的 `NfsArchive` 產生檔案當 oracle | 先紅（`.gen` 與 pack 留著）；拿掉前綴檢查、拿掉 regex、拿掉 `.gen.tmp`、拿掉 `.pack.tmp`、root 不存在的 `return`——五個突變各紅一條 |
| P6 | **docs**：`migrations.md` 一條（空間 ≈2×、開關、**決定 9 的規則**、確認做完的命令）；#867 條目補一行指過來；`configuration.md` §C、`sandbox-host.md` 的 env 表、`sandbox-host-wire.md` 的 persist body；CLAUDE.md 檔案樹那條的最後一句補「pack 快路」半句。#842（備份）還是 draft、它的 runbook 不在 master 上：規則先寫進這條 migrations（「從備份還原 NFS 樹」在列），**後合的那一個 PR** 在備份 runbook 補「load 之後刪 `*.pack.*.tar`」 | 每句對著碼；mkdocs `--strict` 綠 |
| P7 | **第一輪 review（regression／defect）**：`.gen` 的 tmp 共用一個名字，同一個 item 兩個 persist 重疊時後一個 rename 找不到檔 → 帶 uuid；purge 的 regex 跟著改，測試讓 host 自己留下 tmp | 紅：在 `os.replace` 前插入同 item 的另一次換 gen（重現回報的 `FileNotFoundError`）；改回固定名字 → 紅，而且 purge 的 parity 測試一起紅 |
| P8 | **第一輪 review（defect blocker）：打包從 persist 搬到 kill**（決定 3、4 與機制段改寫；死路表最後一列）。`NfsArchive.persist` 回傳「對齊有沒有真的跑」、`NfsArchive.pack` 獨立；controller 的 `pack_on_kill` / `closing`；`_InFlight` 擋封鎖中的 rid；pack tmp 也帶 uuid | 紅：寫回和砍之間來一次 checkpoint 仍然打包；封鎖期間的寫入回 404 `SandboxNotFound`；砍時還有 exec 在跑不打包；對齊被拒不打包；沒要求的砍不打包；沙盒沒 ready 不打包；打包失敗照砍；砍失敗解除封鎖；請求結束後什麼都不留；打包途中寫樹 → pack 一發佈就過期；tar 失敗、tmp 被清掉不發佈；兩次打包不共用 tmp。18 條突變逐條跑，每條都有測試紅 |
| P9 | 推、draft PR（點名動到 `sandbox-host/`；k8s 側**沒有**新項——tar 是 base image 的 GNU tar 1.34，不需要權限）、第二輪 review、CI | PR body 含「prod 怎麼驗證」：回收一次後 `ls $SANDBOX_HOST_NFS_ROOT/<item>.pack.*.tar`；再開的等待時間；host log 沒有 `did not extract` |

**不做**：關機時打包、在 `close`（使用者關閉環境）時打包（可之後：那也是靜止點，但使用者在等回應）、路徑數門檻、壓縮、overlay。

**我不知道、但不影響對錯的**：那個備份的位元組數——決定打包要幾秒、空間吃多少，和這份設計成不成立無關。
