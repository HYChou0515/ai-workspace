# workspace 裡的簡報可以直接看:沙盒轉 PDF、快取在沙盒旁

**狀態:** 已施工(P1–P4,PR #902)。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master` 程式碼確認的事實;〔施工〕= 我定的實作細節,可推翻。

## 1. 問題〔查證〕

workspace 檔案檢視依副檔名挑 renderer(`web/src/renderers/registry.ts`);`.pptx` 沒有對應的,掉到最後的文字編輯器,
畫面是亂碼。AI 用 `make_deck` 做出來的簡報、使用者上傳的簡報,都只能下載回去用 PowerPoint 看。

已經有的零件:

- 沙盒 image 已裝 `libreoffice-impress` + `poppler-utils`(`sandbox-host/Dockerfile`),`make_deck` 用它把 pptx 轉成 PDF。
- 前端已有 `PdfRenderer`(瀏覽器內建 PDF 檢視)。
- 沙盒「workspace 旁的 infra 區」已有先例:`.ready`、`.home` 是 `root/` 的兄弟,不進檔案樹、不被同步、隨沙盒回收;
  `mark_ready` / `is_ready` 是存取它的專用 op,protocol / mock / local / isolated / http client / sandbox-host 各一份。
- 讀檔案不必喚醒沙盒:`WorkspaceFiles` 沙盒冷的時候讀備份快照。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| N1 | **在沙盒轉成 PDF**,不在 API pod 轉:檔案可能是不可信的,沙盒有逐 item 的 uid 與 cgroup 額度、拿不到 API 的憑證。 | 〔user〕「Ok」(沙盒 vs api) |
| N2 | **快取放在沙盒旁的 infra 區,檔名用 pptx 內容的 hash**(`.preview/<sha256>.pdf`):pptx 一改 hash 就變、自然重轉;不進檔案樹、不算 workspace 容量;沙盒回收時一起清掉。 | 〔user〕「2 但應該用 hash 命名」 |
| N3 | **預覽用 PDF 檢視器**(現有 `PdfRenderer`)看;旁邊照其他檔案的「預覽 / 編輯」切換,切到編輯照常顯示現在的東西。放映模式以後再說。 | 〔user〕「先用 PDF 檢視器」「N3 是 preview,edit 還是照常顯示現在的東西」 |
| N4 | **只做簡報**:pptx、ppt、odp(沙盒現有的 impress 就能轉,image 不改)。 | 〔user〕「只有簡報」 |
| N5 | **看得到檔案就能預覽**:只有讀權限的人打開也會觸發轉檔(必要時喚醒沙盒);指令是平台固定的 `soffice`,使用者控制不了。 | 〔user〕「可以,看得到就能預覽」 |
| N6 | **檔案太大先問**:超過門檻時,頁面先問「這個檔案很大,要預覽嗎?」,按「是」才轉檔;沒按就不轉、不喚醒沙盒。 | 〔user〕「如果檔案太大先 頁面詢問是否預覽 是才轉檔」 |

## 3. 施工時我定的事〔施工〕

| # | 定了什麼 | 為什麼 |
|---|---|---|
| D1 | 新 Sandbox op 兩個:`render_preview(handle, path, convert) -> sha \| None`(算 hash、查快取、`convert` 時轉檔)、`get_preview(handle, sha) -> bytes \| None`,只存取 `<item>/.preview/<sha>.pdf`;sha 必須是 64 位小寫 hex(擋路徑穿越)。protocol / mock / local / isolated / docker / http client / sandbox-host 各一份,照 `mark_ready` 的形狀。(原本的 `put_preview` 在 D2 改寫時拿掉了,見下。) | infra 區只能經專用 op 碰。 |
| D2 | 轉檔走既有的 `exec`(isolated / 正式環境是 item 自己的 uid 與 cgroup;時間上限沿用):`soffice --headless --convert-to pdf --outdir <out> <path>`,輸出留在沙盒的 `$HOME` 裡,由 host 讀走。不是新的特權路徑。 | N1 的隔離要真的套用在轉檔上。 |
| D3 | 新路由 `GET /a/{slug}/items/{id}/files/preview?path=`:權限 `read_content`(N5);讀檔算 sha → 快取命中直接回 `application/pdf` → 沒命中才確保沙盒、轉檔、存快取、回 PDF。沙盒冷的時候先查快取不喚醒——但快取跟沙盒一起回收,所以冷沙盒等於沒命中。 | 一次請求一個答案;前端不必輪詢。 |
| D4 | 同一個 (item, sha) 同時只轉一次(per-key lock,pod 內);另一個請求等第一個的結果。 | 兩個人同時打開不跑兩次 soffice。 |
| D5 | 大小門檻(N6)由後端決定:超過門檻又沒帶 `confirm=1` → 回「需要確認」與檔案大小,前端顯示詢問,按「是」再帶 `confirm=1` 重打;快取已經有的直接回,不問。轉檔逾時或失敗 → 回錯誤(說明原因),前端顯示原因與「下載原檔」。門檻先定 20 MB(之後可做成設定)。 | 門檻只寫在一處,前端照後端的回答問;已經轉好的不必再問。 |
| D6 | 前端 `SlidesRenderer`:`registry.ts` 對 pptx/ppt/odp,`editToggle: true`(切到編輯 = 現在的顯示);預覽模式載入中顯示「轉換中…」,成功交給現有 PDF 顯示,太大時顯示詢問(N6),失敗顯示一句說明 + 下載(原因不上畫面,見下方 D5/D6 的修改)。 | N3、N6。 |
| D7 | `docs/migrations.md` 一條:新路由 + sandbox-host 新端點,兩邊要一起部署(API 先上而 host 還舊時,預覽回錯誤、其他功能不受影響)。 | 運營方要知道兩個 image 都得換。 |

施工中確認的限制〔查證〕:`kind: local` 的 userns jail 裡沒有掛 `/proc`,LibreOffice 起不來(`make_deck` 也一樣):
它印出 `/proc not mounted` 之後不會自己結束,要等到轉檔時限(`PREVIEW_TIMEOUT_S`,180 秒)才被收掉;轉檔失敗、說出原因、
不快取。正式環境的 sandbox-host 是 uid + cgroup 隔離、沒有 jail,可以轉。

D2 施工時改過〔施工〕:原本「exec 轉完把 PDF `cat` 到 stdout、再存回快取」會讓整份 PDF 在 host 與 API 之間來回兩趟、
暫存在 API 記憶體(user 指出)。改成 `render_preview(path, convert)`:host 自己算 hash、在 exec 裡把輸出寫進沙盒自己的
`$HOME/.preview-out/<一次一個的隨機名>/`、讀走後寫進 `.preview/<sha>.pdf`;API 只拿 hash,再用 `get_preview` 讀一次。

review 第一輪後定的事〔施工〕(D8–D12):

| # | 定了什麼 | 為什麼 |
|---|---|---|
| D8 | 沙盒寫出來的東西一律當不可信:host 開每一層目錄、開 PDF 都**不跟隨 symlink**,只收「一般檔案、只有一個 link、開頭是 `%PDF`」的 `<簡報檔名>.pdf`;快取是 host **重新寫一份**(先寫暫存檔再 rename),不是把沙盒做的檔案搬過去。`get_preview` 讀快取也一樣不跟隨 symlink。清掉暫存目錄也透過已開的目錄 fd。 | isolated 模式 host 用 root 讀沙盒 uid 寫的東西;jail 模式 infra 區就是 chroot 的 `/`。不這樣做,沙盒在輸出位置放一個 symlink,就能讓 host 把任何檔案(包括別的 item 的檔案)讀出來當成預覽。 |
| D9 | `.preview/` 是 0700、PDF 是 0600。 | 別的 item 的 uid 不能讀這份預覽。 |
| D10 | 每次轉檔用自己的 LibreOffice profile(`-env:UserInstallation=file://<暫存目錄>/profile`)和自己的 `TMPDIR`。 | LibreOffice 不讓兩個程序共用一個 profile:同時轉兩份、或轉檔撞上 `make_deck`,會有一邊失敗。isolated 模式的 `TMPDIR` 是 workspace,暫存檔會跑進檔案樹。 |
| D11 | 轉檔期間每 `log_timeout / 3` 秒印一個點。 | soffice 工作時不印東西;exec 的 idle 上限(預設 60 秒)會在 180 秒總時限之前把它殺掉。 |
| D12 | 要喚醒沙盒來轉檔時,先過每人的沙盒上限(`AdmissionGate.check`,跟 terminal 一樣);快取命中不問。超過上限回 507,前端用 chat / terminal 共用的 `quotaMessage` 說「這個 workspace 擁有者同時開啟的沙盒已達上限」與數字、擁有者到「我的資源」關掉一個,加上下載(額度算在擁有者身上,打開的人可能只是讀者)。 | 預覽跟 terminal 一樣會開一個沙盒,佔的是同一份額度。 |

D5、D6 的「前端顯示原因」也改了〔施工〕:轉換器的錯誤訊息(LibreOffice 的英文 stderr)只留在 API 回應與 log,畫面只說
「這份簡報無法轉成預覽」加上「下載原檔」;拿不到回應(503 等)則說「暫時無法預覽,請稍後再試」。介面不露內部字串。
「已同意預覽大檔」只對同一個路徑、同一份內容有效(轉好之後就收回,之後再問都不帶同意:同一版直接拿快取,內容變了又大就再問一次);簡報被改(turn 結束、terminal、重新整理、有人存檔)時,預覽會重新抓。
D4 的鎖在沒有人持有或等待時就移除,不會隨著打開過的簡報數量一直長大。

Docker backend(開發用)也照 D10 用各自的 profile / 暫存目錄;它的 container 本身就是沙盒,所以讀輸出不必做 D8。
docker 的 `exec_run` 沒有時間上限,轉檔時限在那裡不生效。

review 第二輪後定的事〔施工〕(D13–D15):

| # | 定了什麼 | 為什麼 |
|---|---|---|
| D13 | host 讀、存、送的 PDF 有上限 `PREVIEW_MAX_BYTES`(200 MiB):讀的時候最多讀上限 + 1 個 byte,超過就是轉檔失敗。 | 沙盒可以留一個背景程序,在 soffice 寫完後把 PDF 撐成巨大的 sparse 檔:它不花沙盒什麼,host 卻要整份讀進記憶體(review 重現讀了 2 GB)。只看 `st_size` 不夠,檔案讀的時候還能長大。 |
| D14 | 簡報本身也是沙盒的檔案:簡報**這個名字**是 symlink 或不是一般檔案時,不算 hash、不預覽(畫面說無法轉成預覽,可下載)。只檢查最後一層:路徑中間的資料夾是 symlink 時仍會跟過去——檔案的其他操作(包括「下載原檔」)本來就都這樣,是另一類問題,不在這個 PR 修。 | 跟著 link 讀,host 會讀它指向的東西(例如 `/dev/zero`,讀不完)。 |
| D15 | 寫快取失敗(磁碟滿、名稱被佔)時刪掉寫了一半的暫存檔,回「轉檔失敗」(422),不是 500。 | 寫一半的檔案在 infra 區、不算容量,留著就是漏。 |

## 4. Phases

| Phase | 內容 |
|---|---|
| P1 | Sandbox op `render_preview` / `get_preview`(D1),全部實作 + sandbox-host 端點 + http client |
| P2 | 轉檔服務與路由(D2–D5) |
| P3 | 前端 `SlidesRenderer`(D6) |
| P4 | 文件:`docs/migrations.md`(D7)、相關說明 |
| P5 | review 回合、CI、PR、`/web-demo` |

## 5. 不做

- 放映模式(N3)、編輯。
- Word / 試算表(N4)。
- 跨 item 共用快取(N2:快取在各自的沙盒旁)。
