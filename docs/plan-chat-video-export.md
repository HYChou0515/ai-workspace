# Plan:前端匯出對話——文字(JSON / Markdown)與影片(job 版)

> **狀態:討論完畢、待 user 點頭後動工。** 前作 [plan-chat-video.md](plan-chat-video.md)(PR #817)做了 script 版
> `python -m workspace_app.chat_video`;這一份接它的〈之後的形狀〉,把它做成前端一顆按鈕 + 專門的 worker pod。
> KB chat 不做(它連伺服端 export 都沒有)。

## 需求(user 原話的整理)

1. 「以後 export 點開分文字(這裡順便分成 json 和 md)以及影片(可以選 gif / mpeg 等)也可以選 size、打字速度等等,也可以選從哪裡到哪裡的影片。」
2. 「從新往舊數會比較方便。」
3. 「影片可能要有上限。」
4. 「應該也能要用拉的,然後設倍數或是解析度(dpi)……有時候是需要固定的長寬比然後設文字大小,這都是等價的,只是人好不好設而已;可以前端總是顯示長寬,但是可以有不同的輸入方式。」
5. 「簡單一點,在檔案寫進度就好了。」「取消 = 把進度檔刪掉,想法很好。」心跳「十秒一次就可以了」。
6. 「我相信放在 job 裡面會比較適合,因為 memory 很高,而且又是 chromium 又是 ffmpeg,不會是 sandbox 標配。」
7. 「你要寫好 coordinator,跟著 run consumer 啟動與否;他比較另類需要自己的 docker。」
8. 「這個 job input 歷史對話的部分應該是 json(可能只有部分,但是一個完整 json)以及其他設定與 output 位置,這樣我才能在我需要的時候打後端 api 生成固定字句的 video。」

## 量到的事實(2026-09-19,這台 Debian 11,12 則的範例 `docs/examples/chat-video-sample/`)

- 720p mp4+gif:總共 **48 s**,其中**錄影 = 影片時長 41 s**(頁面即時播放、即時錄),mp4 轉檔 2–3 s、gif 5 s;1080p mp4:57 s。
  ⇒ 最久的一段是錄影,長度 = 影片時長,上限就是 `max_seconds`。
- 峰值記憶體:Chromium(headless_shell)**154–172 MB**、Playwright 錄影用的 ffmpeg 147 MB、node 92 MB、python 107 MB;轉檔的 ffmpeg
  才是大戶——**1080p 單段式 gif 4,546 MB、預設參數 libx264 mp4 1,329 MB**(第一次量寫成「1080p mp4 4,290 MB」是錯的:那次量測腳本
  同時出了 gif 和 mp4,4 GB 的是 gif)。原因:`split → palettegen → paletteuse` 要留住全部 frame 到調色盤算出來;x264 預設 preset +
  執行緒數 = 核心數。**P1 修完再量**:mp4(`veryfast`、解碼與編碼各 2 執行緒)273–320 MB;gif 兩段式 230–640 MB,雙峰——有時前幾秒
  填滿約 50 張 frame 的佇列後持平,120 秒合成片峰值(273)不比 20 秒(185)高 ⇒ 有界、不隨片長長大;執行緒旋鈕和單一輸入(`movie=`)
  都壓不掉雙峰,time-box 到此,規格照量到的邊界寫:integration 測試釘 gif ≤ 1024 MB、mp4 ≤ 512 MB(舊碼 2,119 / 1,260 紅)。
- 檔案大小:720p 41 s mp4 **1.8 MB**、gif **18.2 MB**。
- 依賴大小:Chromium 380–550 MB、`fonts-noto-cjk` 87 MB、ffmpeg + libav 約 20 MB(apt 完整閉包更多)、`playwright` wheel 幾十 MB
  ⇒ image 約 **+0.7–1 GB**。
- 現有碼:`ImportCoordinator`(`kb/import_jobs.py`)是 job 的形狀範本;`WorkspaceFiles.write` 走 `_warm` → `resolve_io_handle`
  **不開 session** 的全域解析(暖的寫活目錄、冷的寫 durable);worker 的精簡組成 `build_bundle` **沒有** `files`,但 blob-gc worker
  走 `build_app`(永遠不 serve)拿 API 的整個組成(`worker.API_REGISTRY_JOBTYPES`);blob-gc 的 k8s 已掛 `/data` + `/scratch` +
  同一個 configMap;`rca-app:latest` 是 `uv sync --frozen --no-dev`,**沒有 extras**;`Message` **沒有 id**(範圍只能用位置);
  檔案路由的動詞:寫既有檔 `edit_content`、mkdir / copy `add_content`(`edit ⊇ add`),copy 先 `read_content` 再 `add_content`。

## 已鎖定的決策

| # | 決定 | 來源 |
|---|---|---|
| 1 | **在 job queue 的 worker pod 跑,不進 sandbox**:依賴 ~1 GB、記憶體 GB 級,不是 sandbox 標配 | 需求 6 |
| 2 | **影片寫進 workspace** `/exports/chat-video/<標題>-<yyyymmdd-hhmm>.<fmt>`:檔案樹看得到、既有 `GET /files/{path}` 下載、可 `show_file`、能讀 item 的人都拿得到、算 workspace 額度;不做 run 模型的 Binary | user 選 A |
| 3 | worker 照 **blob-gc 先例**從 `build_app` 組(`API_REGISTRY_JOBTYPES` 加 `chat-video`),所以有 API 同一個 `WorkspaceFiles`;**不開 sandbox** | 事實 |
| 4 | 影片 route 要 **`read_content` + `add_content`**(transcript 是呼叫者供的;活對話那條路的 `read_chat` 由前端先走的 `export-chat` 把關);route 查一次、worker 寫檔前用 `job.info.created_by` **再查一次**(同 import 的 `_may_write`);前端照三個動詞決定按鈕**顯示與否** | 第 2 題 |
| 5 | **Markdown 匯出在伺服端**:`GET …/export-chat?format=json\|md`(預設 json 不變),`md` 回 `text/markdown` + `<標題>.chat.md`;純函式 `build_chat_markdown` 與 JSON 同一份 messages | 第 3 題 |
| 6 | **範圍 = 訊息位置**,`export-chat` 用絕對位置、0 起算、半開 `[start, end)`,不給 = 全部,不合法 422 一句話;json / md 直接用,影片是前端先拿切好的 JSON 再送 job;有範圍時檔名加 ` (N–M)` | 第 4 題 |
| 6b | **job 的輸入是一份完整的 `.chat.json` + 設定 + 輸出位置,不綁 chat_id**:`POST …/items/{item_id}/chat-video {transcript, options, output_path?}`;API 先把 transcript 寫成 workspace 裡的 **source 檔** `<output>.chat.json`(留著,改了再 POST 就重生),job row 只帶路徑(#723 的 job row 沒圍籬,對話內容不能放上去);worker 用 `parse_chat_export` 讀 source,和 CLI 同一條 | 需求 8 |
| 7 | **前端從新往舊數**:全部 / 最近 N 則 / 自訂(從–到兩個選單,最新在最上),用**開窗當下的快照**換算成絕對位置 | 需求 2 |
| 8 | **尺寸:三種輸入法、一個結果**——比例+解析度滑桿 / 比例+文字大小 / 直接寬×高;永遠顯示「寬 × 高 ・ 文字倍率 ・ 約 N MB」;送出的是 `width` / `height` / `scale`(0 = 自動) | 需求 4 |
| 9 | 露出六個影片選項:尺寸、格式(mp4 / gif / webm 單選)、速度、打字、推進輸入框、最長秒數;其餘 `VideoOptions` 預設 | 第 5 題 |
| 10 | **上限在伺服端**(`config.yaml` `chat_video:` 段、有預設、`config.example.yaml` 附範例、`docs/migrations.md` 記一筆):總像素 ≤ 1920×1080、`max_seconds` ≤ 180、輸出檔 ≤ 100 MB(超過 → 失敗一句話、不寫檔)、既有 workspace 額度、每 item + 每 user 各一支 in-flight(409) | 需求 3 |
| 11 | **進度就是 workspace 裡的一個檔** `<輸出檔>.progress.json`,不做 run 模型、不做狀態 route;完成刪掉、失敗留著 | 需求 5 |
| 12 | **取消 = 刪掉進度檔**;worker **每 10 秒心跳**:讀進度檔(不在 → 取消:錄影關瀏覽器、編碼 kill ffmpeg、都 10 秒內)、在就寫回 `stage / elapsed_seconds / heartbeat_at`;`heartbeat_at` 超過 60 秒沒動 = worker 死了,殘檔可覆蓋 | 需求 5 |
| 13 | **自己的 image** `rca-app-chat-video`(Dockerfile 加 stage,不塞進 `rca-app`);k8s 一顆 `rca-worker-chat-video` 照 blob-gc 抄;all-in-one 時 API 進程自己吃 job,沒裝工具 → 進度檔寫那句安裝提示、不炸 API | 第 7 題 |

## 決定的做法

### 資料流

```
前端 Export ▾ ── 文字 JSON/MD ──► GET  …/chats/{id}/export-chat?format=&start=&end=   (read_chat) ──► 直接下載
           └── 影片 ── ① GET export-chat?start=&end=(切好的 JSON) ──────────────────────────────┐
                       ② POST …/items/{item_id}/chat-video {transcript, options, output_path?}  ◄──┘
                          (read_content + add_content;你自己打 API 也是這一條:transcript 可以是手寫的)
                          a. parse_chat_export(transcript)、check_limits(options)、output_path 在 workspace 內且不存在
                          b. 同 item 同 user 有活的進度檔 → 409
                          c. build_timeline → expected_seconds
                          d. files.write(<output>.chat.json = source)、files.write(<output>.progress.json, stage=queued)
                          e. enqueue ChatVideoJob(payload = 路徑們 + options, partition_key=item_id)
                          f. 202 {output_path, source_path, progress_path, expected_seconds}
                    ┌───────────────────────────────────────────────────────────────────┐
                    │ worker `chat-video`(從 build_app 組;或 all-in-one 的 API 進程)        │
                    │ _handle: 再授權(created_by)→ files.read(source) → parse_chat_export  │
                    │   → 對 referenced_paths() 逐一 files.read(item, path)(有大小上限)   │
                    │   → 心跳 task(每 10 s):讀進度檔;不在 → 取消旗標;在 → 寫 stage/elapsed │
                    │   → to_thread(render_chat_video(…, should_stop=旗標))                 │
                    │       record():每 2 s 切片等 done,切片之間看旗標 → 關瀏覽器            │
                    │       encode():Popen + 每秒 poll,看旗標 → kill                        │
                    │   → 輸出 > max_output_bytes → 失敗一句話                               │
                    │   → ensure_room_for(len) → files.write(output) → 刪進度檔(source 留著)  │
                    │   失敗:進度檔 stage=failed + error 一句話,留著                         │
                    └───────────────────────────────────────────────────────────────────┘
前端:輪詢 GET /files/<progress_path>(1 s → 8 s 退避)→ header 狀態列「🎬 製作中 · 預計 41 s」+ 進度條(elapsed/expected)
      進度檔消失且輸出檔存在 → 「已存到 … [開啟] [下載]」+ invalidate 檔案樹;failed → 一句話 + [重試];[取消] = DELETE 進度檔
```

### 進度檔(`<output>.progress.json`,JSON,人看得懂)

```json
{"stage": "rendering", "expected_seconds": 41, "elapsed_seconds": 20,
 "started_at": "2026-09-19T03:10:00Z", "heartbeat_at": "2026-09-19T03:10:20Z",
 "output_path": "/exports/chat-video/OOM-調查-20260919-0310.mp4",
 "requested_by": "hychou", "error": ""}
```
`stage ∈ queued | rendering | encoding | writing | failed`(`done` 不會被看到——完成即刪)。

### 模組

```
src/workspace_app/chat_video/
  options.py    VideoOptions(既有)+ ChatVideoLimits(config 段的 struct)+ check_limits(options, limits) -> None | 422 句
  render.py     record(html, options, workdir, *, expected_ms, should_stop)  切片等待;encode(src, fmt, out, *, should_stop)  Popen 輪詢
  service.py    render_chat_video(…, should_stop=None)(既有簽名加一個 callback)
  jobs.py       ChatVideoPayload(item_id, source_path, output_path, progress_path, options)/ ChatVideoJob / ChatVideoCoordinator(enqueue / _handle / 心跳 / 進度檔 / 授權)
  progress.py   Progress struct + read/write/delete(全部走 WorkspaceFiles)
src/workspace_app/kb/chat_export.py   build_chat_markdown(title, messages);slice_messages(messages, start, end)
src/workspace_app/api/chat_routes.py  export-chat 加 format/start/end;新 POST …/items/{item_id}/chat-video(item 層級,不綁 chat)
src/workspace_app/coordinators.py     bundle 加 chat_video(受 run_consumers 控制,同其他)
src/workspace_app/worker/__init__.py  _JOBTYPE_ATTR 加 "chat-video";API_REGISTRY_JOBTYPES 加 "chat-video"
config/schema.py                       chat_video: {max_pixels, max_seconds, max_output_bytes, heartbeat_seconds=10, stale_after_seconds=60}
docker/Dockerfile                      stage `chat-video` → rca-app-chat-video
kubernetes/base/workers.yaml           rca-worker-chat-video
web/src/…                              ExportMenu + ExportDialog(格式 / 範圍 / 尺寸三模式 / 影片選項)+ VideoProgress(輪詢進度檔)
```

### Markdown 格式

```
# <標題>

### 👤 <author 或 User>
<content>

### 🤖 <author 或 AI>
> 💭 <reasoning>(有才出現)

<content(原本就是 markdown,原樣)>

### 🔧 <tool_name>
```json
<tool_args>
```
```
<content,截到 600 字(和影片同一個 tool_output_chars)>
```
- 📎 <宣告的檔案 path>(`[shown-files]` 宣告拿掉、檔案列成清單)

### ⚠️ 錯誤(<error_kind>)
<content>
```
`stopped_reason` 有值在該則末尾加 `_（已中止：…）_`;`system` / 其他 role 一行斜體。

### 尺寸的三種輸入法(前端)

| 方式 | 使用者設 | 算出 |
|---|---|---|
| 比例 + 解析度滑桿 | 16:9 / 1:1 / 9:16;桿 0.5×–1.5×(底 1280×720) | `width`,`height`;`scale=0`(自動 = 框 / 720p) |
| 比例 + 文字大小 | 比例;小 / 中 / 大 / 特大 = 0.8 / 1 / 1.3 / 1.6 | `scale`;`width`,`height` = 720p 版面 × 倍率(放得下標準版面) |
| 直接寬 × 高 | 寬、高(+ 可選文字大小) | 三值直接送 |

結果列永遠顯示「W × H ・ 文字 k× ・ 約 N MB」;估檔案大小用量到的 720p 數(mp4 0.045 MB/s、gif 0.45 MB/s)× 像素比 × 預估秒數,標「約」。

## 知情取捨

- **影片是聊天視窗畫的,不是比它好看的**(#817 第八輪的結論):圖片型別由檔名決定、和檔案路由共用同一函式;這裡不變。
- 進度不是真百分比(錄影是一段阻塞呼叫),是 `elapsed / expected`,每 10 秒真的前進;文案用「預計」。
- 進度檔在檔案樹裡看得到(在 `/exports/chat-video/` 底下),失敗的會留著——這是刻意的:人看得到、也刪得掉。source 檔 `<output>.chat.json` 也留著:它就是「固定字句」,改了再 POST 一次就重生;不想要就刪。
- 同一 item 第二次匯出會排在第一次後面(partition_key = item_id)但 route 先 409——簡單優先。
- 匯出對話框沒有 `useDirtyClose`:它裝的是**選項**不是未存的工作,Escape / ✕ 丟掉的只是幾個下拉的選擇;`closeOnBackdrop` 照 `ModalShell` 預設 false。
- 不做:自訂路徑、多格式一次出、在對話串裡點選範圍、進度 SSE。

## Phases(每個 = 一個 commit,TDD;每個修法先有會紅的測試)

### P1 — ffmpeg 記憶體 ✅
- 量:`encode` 對 41 s 1080p 的 webm,gif 4,546 / mp4 1,329 MB(見〈量到的事實〉;`-vsync cfr` 沒用,時間軸本來就規則)。
- 修:gif **兩段式**(`palettegen` 出 png,再 `movie=` 載入 `paletteuse`);mp4 `-preset veryfast`;解碼與編碼各 `-threads 2`。
- 釘:`test_encoding_a_1080p_clip_stays_within_the_measured_bound`(integration,`ps` 取樣 ffmpeg 子行程)gif ≤ 1024、mp4 ≤ 512 MB;
  數字寫進 `docs/chat-video.md`〈要多少資源〉。

### P2 — 文字匯出:Markdown + 範圍
- `kb/chat_export.py`:`slice_messages(messages, start, end)`(驗證 → `ValueError` 一句話)、`build_chat_markdown(title, messages)`;`chat_export_filename` 加 `suffix` 與範圍後綴。
- route `export-chat?format=&start=&end=`;422 一句話;`Content-Disposition` 兩種副檔名。
- 測試:格式表(每種 role 一列、reasoning、tool 截斷、shown-files 清單、stopped、error)、範圍邊界(0 / 全部 / 越界 / start≥end)、route 兩種 format。

### P3 — 上限 config
- `config/schema.py` 加 `chat_video`(預設如決策 10);`config.example.yaml` 附範例段;`docs/migrations.md` 記一筆(default-on、不填照預設)。
- `options.py`:`ChatVideoLimits` + `check_limits`;測試每條上限的邊界(等於過、超過一句話)。

### P4 — 進度檔 + 取消旗標
- `chat_video/progress.py`:`Progress` struct、`write(files, item, path, progress)` / `read` / `delete`;`is_alive(progress, now, stale_after)`。
- `render.py`:`record(…, should_stop)` 切片等待(2 s)+ `encode(…, should_stop)` Popen 輪詢 + kill;`service.render_chat_video(…, should_stop)`。
- 測試:假 playwright 在第 N 片翻旗標 → 瀏覽器被關、丟 `Cancelled`;假 ffmpeg(sleep 的 subprocess)被 kill;心跳寫入序列。

### P5 — coordinator + worker
- `chat_video/jobs.py`:`ChatVideoPayload(item_id, source_path, output_path, progress_path, options)`、`ChatVideoJob(Job[ChatVideoPayload])`、`ChatVideoCoordinator(spec, *, files, limits, message_queue_factory, superusers, permission_of)`:
  `enqueue(item_id, transcript, options, output_path, user)`(驗 transcript、409 規則、寫 source 檔、進度檔 queued、job);`_handle()`(再授權、`files.read(source)` → `parse_chat_export`、讀 assets、心跳 task、`to_thread(render)`、大小上限、`ensure_room_for`、寫檔、刪進度檔;任何失敗 → 進度檔 failed 一句話)。
- `coordinators.py` bundle 加 `chat_video`,**受 `run_consumers` 控制同其他**;`worker/__init__.py` 兩張表各加 `chat-video`;`worker.build_coordinator` 對它走 `build_app`。
- 測試:enqueue 的 409 / 過期殘檔可覆蓋;`_handle` 全路徑(假 render);再授權失敗 → failed、不寫;取消 → 不寫、進度檔不重建;`ensure_room_for` 不足 → failed 一句話;all-in-one 沒工具 → failed 一句話。

### P6 — route
- `POST /a/{slug}/items/{item_id}/chat-video`(`read_content` + `add_content`、body `{transcript, options, output_path?}`、`parse_chat_export` 驗 transcript → 422 一句話、`check_limits`、`output_path` 在 workspace 內且不存在、`build_timeline` 算 `expected_seconds`、呼叫 `enqueue`、202)。
- 測試:兩動詞各缺一個 → 403;壞 transcript / 上限 / 路徑逃逸或已存在 → 422;in-flight → 409;202 的 body 四個路徑;手寫的三則 transcript 也能排。

### P7 — 前端
- `ExportMenu`(Export ▾:文字 JSON / 文字 Markdown / 影片…)→ `ExportDialog`(格式、範圍三選一 + 從/到選單最新在上、尺寸三模式 + 結果列、六個影片選項);`api/workflows.ts` 的 `fetchChatExport` 加 format / range;新 `startChatVideo(slug, itemId, transcript, options)`——影片是 **先 fetch 切好的 JSON、再 POST**,前端用的就是對外那條 API。
- `VideoProgress`(header 狀態列,`useQuery` 輪詢 `GET /files/<progress_path>`,退避 `pollAfter`;done / failed / 取消 = DELETE 進度檔);按鈕依三動詞顯示。
- i18n 兩種語系;測試:範圍換算(快照、倒數 → 絕對)、尺寸三模式的換算表、進度輪詢的三種結局、按鈕顯示條件。

### P8 — image + k8s + 文件 + 親眼驗收
- `docker/Dockerfile` 加 stage `chat-video`;`kubernetes/base/workers.yaml` 加 `rca-worker-chat-video`(limit 以 P1 量到的為準);`docs/deployment.md` §11 worker 清單加一顆、`docs/chat-video.md` 加「從前端匯出」一節、`docs/migrations.md`。
- 本機 all-in-one(`run_consumers: true`,裝好 extra + Chromium + ffmpeg)從 UI 按到底:選範圍 → 排 job → 進度前進 → 影片出現在檔案樹 → 開啟;再試取消(刪進度檔)與失敗(上限)兩條。
- **Web demo 給 user 看**(`/web-demo`,真瀏覽器錄 GIF):① Export ▾ → 文字 Markdown → 下載、打開看格式;② Export ▾ → 影片 → 選「最近 5 則」、比例 + 解析度滑桿拉到 1080p、格式 mp4 → 送出 → header 進度列每 10 秒前進 → 完成 →「已存到 …」→ 點開影片播;③ 再排一支、在檔案樹刪掉進度檔 → 10 秒內停;④ 用 curl 對 `POST …/chat-video` 送手寫三則的 transcript → 三個檔出現在樹裡。GIF 附在 PR 裡、也傳給 user。**做完 = user 看得到、按得動;GIF 沒錄到的功能不算做完。**

## 驗收

- 前端 Export 選單三種都能出檔;md 貼進報告可讀;範圍「最近 5 則」出的內容就是最新 5 則。
- **user 看過 web demo 的 GIF**(P8 的四段),而且是在 PR 合併之前。
- 影片從 UI 排隊到出現在 `/exports/chat-video/`,進度條每 10 秒前進,刪進度檔 10 秒內停;上限違反時是一句話不是 traceback。
- 用 curl 對 `POST …/chat-video` 送一份手寫三則的 transcript,也出得了影片(source / progress / mp4 三個檔都在樹裡)。
- `run_consumers: false` + `worker chat-video` 的 pod-split 走通(本機兩個進程);`rca-app` image 大小不變。
- ffmpeg 峰值 RSS 量到的數字 ≤ workers.yaml 的 limit;`ruff` / `ty` / targeted 測試綠;`mkdocs --strict` 綠。
