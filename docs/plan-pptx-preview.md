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
| D1 | 新 Sandbox op 兩個:`get_preview(handle, sha) -> bytes \| None`、`put_preview(handle, sha, data)`,只存取 `<item>/.preview/<sha>.pdf`;sha 必須是 64 位小寫 hex(擋路徑穿越)。protocol / mock / local / isolated / http client / sandbox-host 各一份,照 `mark_ready` 的形狀。 | infra 區只能經專用 op 碰;只存 bytes,不在 host 端跑程式。 |
| D2 | 轉檔走既有的 `exec`(uid / cgroup / 時間上限都沿用):`soffice --headless --convert-to pdf --outdir <tmp> <path>` 後把 PDF 讀回來。不是新的特權路徑。 | N1 的隔離要真的套用在轉檔上。 |
| D3 | 新路由 `GET /a/{slug}/items/{id}/files/preview?path=`:權限 `read_content`(N5);讀檔算 sha → 快取命中直接回 `application/pdf` → 沒命中才確保沙盒、轉檔、存快取、回 PDF。沙盒冷的時候先查快取不喚醒——但快取跟沙盒一起回收,所以冷沙盒等於沒命中。 | 一次請求一個答案;前端不必輪詢。 |
| D4 | 同一個 (item, sha) 同時只轉一次(per-key lock,pod 內);另一個請求等第一個的結果。 | 兩個人同時打開不跑兩次 soffice。 |
| D5 | 大小門檻(N6)由後端決定:超過門檻又沒帶 `confirm=1` → 回「需要確認」與檔案大小,前端顯示詢問,按「是」再帶 `confirm=1` 重打;快取已經有的直接回,不問。轉檔逾時或失敗 → 回錯誤(說明原因),前端顯示原因與「下載原檔」。門檻先定 20 MB(之後可做成設定)。 | 門檻只寫在一處,前端照後端的回答問;已經轉好的不必再問。 |
| D6 | 前端 `SlidesRenderer`:`registry.ts` 對 pptx/ppt/odp,`editToggle: true`(切到編輯 = 現在的顯示);預覽模式載入中顯示「轉換中…」,成功交給現有 PDF 顯示,太大時顯示詢問(N6),失敗顯示原因 + 下載。 | N3、N6。 |
| D7 | `docs/migrations.md` 一條:新路由 + sandbox-host 新端點,兩邊要一起部署(API 先上而 host 還舊時,預覽回錯誤、其他功能不受影響)。 | 運營方要知道兩個 image 都得換。 |

施工中確認的限制〔查證〕:`kind: local` 的 userns jail 裡沒有掛 `/proc`,LibreOffice 起不來(`make_deck` 也一樣);
轉檔會失敗並說出原因(`/proc not mounted`)、不快取。正式環境的 sandbox-host 是 uid + cgroup 隔離、沒有 jail,可以轉。

D2 施工時改過〔施工〕:原本「exec 轉完把 PDF `cat` 到 stdout、再存回快取」會讓整份 PDF 在 host 與 API 之間來回兩趟、
暫存在 API 記憶體(user 指出)。改成 `render_preview(path, convert)`:host 自己算 hash、在 exec 裡把輸出寫進沙盒自己的
`$HOME/.preview-out/<sha>/`、再搬進 `.preview/<sha>.pdf`;API 只拿 hash,再用 `get_preview` 讀一次。`put_preview` 不對外。

## 4. Phases

| Phase | 內容 |
|---|---|
| P1 | Sandbox op `get_preview` / `put_preview`(D1),全部實作 + sandbox-host 端點 + http client |
| P2 | 轉檔服務與路由(D2–D5) |
| P3 | 前端 `SlidesRenderer`(D6) |
| P4 | 文件:`docs/migrations.md`(D7)、相關說明 |
| P5 | review 回合、CI、PR、`/web-demo` |

## 5. 不做

- 放映模式(N3)、編輯。
- Word / 試算表(N4)。
- 跨 item 共用快取(N2:快取在各自的沙盒旁)。
