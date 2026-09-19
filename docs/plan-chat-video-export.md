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
`stage ∈ queued | rendering | encoding | failed`(`done` 不會被看到——完成即刪;寫檔是一次 facade 呼叫,心跳在算圖結束就停,所以沒有 `writing` 這一格)。

### 模組

```
src/workspace_app/chat_video/
  options.py    VideoOptions(既有)+ check_limits(options, max_pixels=, max_seconds=) -> None | 422 句
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

### P3 — 上限 config ✅
- `config/schema.py` `ChatVideoSettings` + `Settings.chat_video`;loader whitelist + `_build`(測試:預設值 = 量到的上限、寫了會被建出來);
  `configs/config.example.yaml` 附註解段(去掉 `#` 餵 loader 驗過);`docs/configuration.md` 表加一列;`docs/migrations.md` 加 #823 的條目
  (四格;裡面提到後面 phase 才有的行為——P9 收尾時逐句回驗)。
- `options.py` `check_limits(options, *, max_pixels, max_seconds)`(不依賴 config 模組,收整數):等於過、超過 `ValueError` 一句話點名上限與數字
  (`1922×1080 is 2,075,760 pixels; at most 2,073,600 (1920×1080)`;1921 會先被「兩邊都要偶數」擋下——mp4 編碼器的規則);`max_output_bytes` 在 worker 寫檔前才能查,P5。

### P4 — 進度檔 + 取消旗標 ✅
- `chat_video/progress.py` 只做純的部分:`Progress` struct(msgspec,縮排 JSON 給人看)、`loads` 壞檔 → `ValueError`、
  `is_alive(progress, now, stale_after_seconds)`(running 且心跳在期限內;`failed` 不算持有)、`paths_for(output)` 給 source / progress 兩個檔名;
  讀寫走 `WorkspaceFiles` 是 P5 coordinator 的事。
- `render.py`:`Cancelled`、`StopCheck`;`record(…, should_stop)` 每 `RECORD_SLICE_MS`(2 s)切片等 `done`、切片之間問旗標 → 關瀏覽器、丟 `Cancelled`;
  `encode(…, should_stop)` 改 `Popen` + 每秒 `wait(1)`:stop → kill、超時 → kill + 一句話、非零 → stderr 尾巴;任何拒絕都把半寫的輸出刪掉。
  `service.render_chat_video(…, should_stop)` 直傳給兩者。
- 測試:假 playwright 記每次 `timeout`(釘住切片長度)與 close 次數;假 `Popen` 一開始就寫半成品(釘住清理);8 個突變體各紅自己的測試
  (兩個第一版沒抓到:「不切片」和「留半寫檔」——替身看不到那個性質,補上才紅)。套件 100%。

### P5 — coordinator + worker ✅
- `chat_video/jobs.py`:`ChatVideoPayload(item_id, source_path, output_path, progress_path, options)`、`ChatVideoJob(Job[ChatVideoPayload])`(`partition_key = item_id`)、`ChatVideoCoordinator(spec, *, limits: ChatVideoSettings, message_queue_factory, superusers, render, now)` + `set_files(files)`(post-build 注入,同 eval 的 `set_retriever`;`limits` 直接吃 config 的 dataclass,不再抄一份 `ChatVideoLimits`):
  `enqueue(item_id, title, messages, options, output_path, expected_seconds, user)`(409 兩條規則都問 `progress.is_alive`:同輸出有活的進度檔 / 同人在此 item 有活的 job——後者從 queue 的 active rows 找、以進度檔為準;過期或不是我們寫的檔可覆蓋;寫 source 檔 + queued 進度檔 + job row 只帶路徑);
  `_handle()` 把 `_run()` 丟到**一個** loop 上跑(API 下是 lifespan 捕到的 loop;worker 沒 running loop 就自己開一條 thread 的 loop,整個消費期共用一條——production sandbox 是 `kind: http`,facade 透過同一個 `httpx.AsyncClient` 打它,連線池屬於開它的 loop,一 job 一 loop 的第二支 job 會用到已關 loop 上的連線);
  `_run()`:再授權(`_may` = `load_access_facts` + `authorize` 兩個 verb,少一個都拒)、`parse_chat_export` 讀 source(壞掉/不見 → failed 一句話)、assets 先問 `file_size` 再讀(CLI `load_assets` 的規則:超過 `max_asset_bytes` 不進記憶體)、心跳 task 每 `heartbeat_seconds` 重寫 stage/elapsed、檔不見 → `stop.set()`、`to_thread(render, should_stop=stop.is_set, on_stage=…)`、`Cancelled` → 什麼都不寫、其他例外 → failed 一句話(all-in-one 沒 Chromium/ffmpeg 就是 `ensure_tools` 那句)、算完再看一次進度檔(在最後一次心跳與算完之間被刪也要丟掉)、`max_output_bytes` → failed 一句話、`files.write(output)`(額度是 facade 自己的規則,一次寫就是那個閘,沒有再前置一個 `ensure_room_for`)被拒 → failed `could not write …`、成功刪進度檔。`fail()` 在進度檔已被刪時不重建。
- `coordinators.py` bundle 加 `chat_video`(永遠建,`chat_video_settings` 從 `settings.chat_video`)、`create_app` 注入 facade + `app.state.chat_video_coordinator`、lifecycle 在 `run_consumers` 下啟動 + 進 drain 清單;`worker/__init__.py` 兩張表各加 `chat-video`(`API_REGISTRY_JOBTYPES`:它要的是 API 組出來的那個 facade,只有 `create_app` 會組);`kubernetes/base/workers.yaml` 加 `rca-worker-chat-video`(manifest 守衛要求每個 JobType 都有 Deployment,所以放在這一步;image `rca-app-chat-video:latest`,memory request 1Gi / limit 2Gi 由 P1 量到的數字推:錄影 Chromium 154–172 MB + 錄影 ffmpeg 147 MB,編碼 mp4 273–320 MB / gif 230–640 MB,兩段不重疊)。
- 測試(`tests/chat_video/test_jobs.py` 16 個函式 19 個案例、`test_consumer_gate` +2、`test_worker` +1 走 `build_coordinator` 真門):27 個突變體(含對照組)各紅自己那條;jobs.py / coordinators.py / worker 100%。
- 第一版兩個洞在寫測試時發現:`stage[0]="writing"` 在心跳已停之後才設、沒人看得到(拿掉 `writing` 這一格);取消測試分不出「被旗標停下」和「等到放棄」(假 render 記 `stopped`)。

### P6 — route ✅
- `api/chat_video_routes.py`:`POST /a/{slug}/items/{item_id}/chat-video`(`read_content` + `add_content` 各問一次 `locator.require_access`、body `{transcript, options?, output_path?}`):transcript 走同一個 `parse_chat_export` → 422 一句話;options 用 `msgspec.convert` 進 `VideoOptions`(struct 自己的 `__post_init__` 句子)→ 422;`output_path` 走 file routes 共用的 `_workspace_path`(`..` → **400**,同 mkdir/move,不是 422——同一個守衛同一個答案)、**副檔名決定格式**(同 CLI 的 `-o demo.gif`;不在 gif/mp4/webm → 422)、不給就 `/exports/chat-video/<safe_stem(title)>-<YYYYMMDD-HHMMSS>.<fmt>`(`safe_stem` 從 `chat_export_filename` 抽出來共用,一條規則);`check_limits` 對 `settings.chat_video` → 422;路徑已有檔 → **409** `file exists at …`(file routes 對「目標已存在」的答案);`build_timeline` 算 `expected_seconds`;`enqueue`;`InFlight` → 409;寫完兩個檔各 publish 一個 `FileChanged` 讓檔案樹重抓;202 `{output_path, source_path, progress_path, expected_seconds}`。
- 測試(`tests/api/test_chat_video_routes.py` 9 個函式 12 個案例,app 開 `run_consumers=False` 因為本機有 Chromium 會真的開始錄):手寫三則中文 transcript 排進去、三個路徑與檔案樹裡的兩個檔;副檔名決定格式;兩動詞各缺一個 → 403;壞 transcript / 壞 option / 超上限 → 422 各自的句子;`..` → 400、壞副檔名 → 422;已存在 → 409;in-flight → 409。13 個突變體(含對照組)各紅自己那條;route 與 `chat_export.py` 100%。
- 探針踩到一個坑:同秒同大小的突變體(`202`→`200`)還原後 Python 仍信任舊 pyc,三條測試紅得像程式壞了;探針 restore 後刪該模組的 pyc。

### P7 — 前端的純粹部分 + 兩條伺服端小規則 ✅
- `web/src/lib/chatExportRange.ts`:`absoluteRange(total, choice)` 把對話框「從新往舊」的選擇(全部 / 最近 N 則 / 自訂 from–to,1 = 最新)換成伺服端的 `[start, end)`;整串 = `null`(不帶參數、檔名不帶範圍)。文字匯出與影片切片都用同一個函式,所以「最近 5 則」兩邊一定是同五則。
- `web/src/lib/videoSize.ts`:三種輸入 → 同一個 `{width, height, scale, scaleIsAuto}`:`resolution`(比例 + 短邊 480p…2160p,文字倍率 = player 的自動規則 `max(1, min(w/1280, h/720))`,鏡射 `player.ui_scale` 並釘住它文件裡的三個例子)、`text`(比例 + 文字倍率,畫面跟著放大、`scale` 釘死——自動規則對 4:3 的 1440×1080 只給 1.125,所以要釘)、`custom`(手打寬高)。每個輸出都是偶數。`estimateMegabytes(fmt, pixels, seconds)` 從 P1 量到的兩個點(720p / 1080p 各 41 秒)推,測試釘住它能還原那四個數。
- `web/src/api/chatVideo.ts`:`fetchChatTranscript`(匯出 JSON 解析——範圍選單數的、影片切的都是它)、`startChatVideo`(POST,拒絕時丟伺服端那句話)、`fetchChatVideoLimits`(GET 同路徑:表單只提供這部署允許的尺寸);`fetchChatExport` / `downloadChatExport` 加 `{format, range}`(md 回 `text/markdown` 也驗)。
- 伺服端:`VideoOptions` 多一條「寬高都要偶數」——親手試過 `libx264 + yuv420p` 對 1001×601 直接拒絕(`width not divisible by 2`),而且是在整段錄影跑完之後;表單只給偶數,這條是給 CLI 和 API 呼叫者的。上限例子改用 1922×1080。`GET …/chat-video` 回三個上限(P8 接)。
- 測試:vitest 8 + 8 + 5(含 workflows 既有 11 條一起綠);pytest options 30。

### P8 — 前端的對話框、進度列、接線 ✅
- `components/ExportDialog.tsx`:Export 按鈕改開一個對話框(不是下拉再對話框——header 的動作列在窄欄會降成 ⋯ 選單,子選單在那一層做不出來;一個對話框三種格式,「文字 JSON / 文字 Markdown / 影片」是最上面一組 radio,同樣是 user 說的「點開分文字…以及影片」)。範圍三選一:全部(N 則,N 來自匯出的 JSON,不是猜的)/ 最近 N 則 / 自訂 from–to(選單標籤 `#k · 👤/🤖 前 24 字`,#1 = 最新、#N = 最舊);影片段落:格式 mp4/gif/webm、尺寸三模式(比例＋解析度 / 比例＋文字大小 / 寬×高)+ 永遠顯示的結果列 `W×H・文字 s×・最多約 N MB`、六個節奏旋鈕(打字、回覆、工具停頓、zoom、speed、最長秒數)。解析度與文字大小的選單只列這部署 `max_pixels` 允許的(`GET …/chat-video`);手打寬高超過就顯示上限並鎖送出;`max_seconds` 送出前夾到部署上限。影片 = 先拿匯出 JSON、用同一個 `absoluteRange` 切、再 POST;`scale` 自動就送 0(交給 player 的規則)、文字大小模式才送數字。`useDirtyClose` 守 Escape / ✕ / 取消三個出口。
- `components/VideoProgress.tsx`:header 下一列,`useQuery` 輪詢進度檔(1 s × 5 → 2 → 4 → 8 s 封頂;`pollDelay`),三種結局都從檔案本身讀:`failed` → 那句話 + 關閉(順手刪掉 worker 留下的檔);檔案不見且沒按取消 → 完成(路徑、開啟、重抓 `qk.files`,並停止輪詢);按取消 = 刪進度檔 → 之後的不見讀成「已取消」。
- 接線:`useItemAccess.canAddContent`(`canAddItemContent`,單一 verb,不是 `canWrite` 的聯集——只有 edit_content 的人聯集說可以、route 會 403);`WorkspaceShell` 算 `canExportVideo = canSeeFiles && canAddContent` → `ItemChatShell`(兩個 render 點)→ `AgentPanel` → `AgentHeader`;沒有就把影片那個 radio 鎖住並寫原因。i18n 兩語系 54 個 key;`styles/export-dialog.css` 自己的 class(沿用 `.chat-share__*` 會零樣式)。
- 測試:`ExportDialog.test.tsx` 11 條(三種格式各自的呼叫、範圍換算、上限過濾、鎖定、伺服端句子、dirty / clean)、`VideoProgress.test.tsx` 5 條(三種結局、輪詢停止、退避表)、`AgentHeader.test.tsx` 改為「開對話框、帶 slug / chatId / 影片閘」、`itemPermission` / `useItemAccess` 各加 canAddContent。16 個突變體(含對照組)各紅自己那條;web 全套 245 檔 2169 條綠(順手跑的,不是 gate)。
- 拿掉一個守不到東西的守衛:`sawFailed`(failed 之後輪詢就停、關閉會卸載元件,所以「failed 之後不見」到不了)。
- 已知未做:重新整理頁面後 header 的進度列不會回來(進度檔還在樹裡、可以手動刪);做影片的期間沒有第二顆進度列(route 對同人同 item 409)。

### P9 — image + 文件 + 親眼驗收 + web demo ✅
- `docker/Dockerfile` 加 stage `chat-video`(`FROM app`:`uv sync --extra chat-video` + apt ffmpeg + `playwright install --with-deps chromium`,`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`,CMD 是 worker;`docker build --target chat-video -t rca-app-chat-video:latest`)。本機 build 卡在 app stage 的 LibreOffice apt 下載(75 分鐘沒過),**image 大小沒量到**,runbook 照實寫。
- `docs/deployment.md` §11 worker 清單加 `chat-video` + 一段「也組 API 那套、自己的 image、all-in-one 要跑 `rca-app-chat-video`」;`docs/chat-video.md` 加「從聊天視窗匯出」+「從 API 出固定字句的影片」(curl 例子的 `expected_seconds` 是跑 `build_timeline` 算的 7);`docs/migrations.md` 的 #823 條目逐句回驗(「變成選單」→「開一個對話框」;漏 worker 的症狀改成前端真的會顯示的那句;確認做完改成 UI 真的字)。
- 202 多回 `stale_after_seconds`;前端進度改用伺服端這個數判「worker 沒有回應(N 秒沒心跳)」——runbook 寫的症狀,前端本來根本不會顯示。
- **親眼驗收 / web demo**(真 app all-in-one 在 :8321、假 OpenAI 端點只換模型吐的字、其餘全真;`tmp/demo/tour.py` 四段一鏡到底):① 匯出 → Markdown → 最近 4 則 → 下載(檔案是第 3–6 則,可讀);② 匯出 → 影片 → 最近 5、1080p、mp4 → 排隊中 → 錄影中 → 編碼中 → 已存到 → 開啟(2.4 MB、h264 1920×1080、22.3 s,ffprobe 過);③ 再排 gif、按取消 → 已取消(沒有 gif、沒有進度檔);④ curl 手寫三句 → `videos/handwritten.mp4` 出現、Ctrl+P 打開。GIF(1.6× 速)+ webm 給了 user。
- demo 抓到的東西(review 沒看到的):**「開啟」把 mp4 丟進文字編輯器**(2.4 MB 亂碼 + invisible-unicode 警告)→ 加 `renderers/VideoRenderer.tsx`(`<video controls>` 串檔案路由,不經 editor buffer;registry `video: mp4 / webm`,gif 留給 image);錄影用的 open-source Chromium 解不了 h264 所以 GIF 裡播放器是黑的,一般瀏覽器會播。
- user 看了 demo 的兩個回饋都做了:**對話框照 PR #825 的樣子**(Tools modal 的框:480px、`<strong>` 標題 + 12px 說明、`.btn` 取消/主要 footer、沒 ✕ 沒 icon;欄位 label 在上 + house `.input`、helper `.detail`、兩欄 grid;`.input` 這條 base.css 規則和 #825 加的一字不差,先合的留、後合的解一個顯而易見的衝突);**進度不擺中間**——改成綠點右邊的膠囊,chat 欄放不下時 header 把它整顆換行、`margin-left: auto` 靠右貼在 ⋯ 與綠點下面;路徑從左省略(`direction: rtl`,所以顯示 `relPath`,不然開頭的 `/` 會被 bidi 搬到尾巴)。
- 探針踩到的坑:`/tmp` 100%(別的 session 留的 pytest basetemp 與孤兒 blob 目錄)讓 pytest 的輸出 ENOSPC;只清自己 user 的、一天以上的。

## 驗收

- 前端 Export 選單三種都能出檔;md 貼進報告可讀;範圍「最近 5 則」出的內容就是最新 5 則。
- **user 看過 web demo 的 GIF**(P9 的四段),而且是在 PR 合併之前。
- 影片從 UI 排隊到出現在 `/exports/chat-video/`,進度條每 10 秒前進,刪進度檔 10 秒內停;上限違反時是一句話不是 traceback。
- 用 curl 對 `POST …/chat-video` 送一份手寫三則的 transcript,也出得了影片(source / progress / mp4 三個檔都在樹裡)。
- `run_consumers: false` + `worker chat-video` 的 pod-split 走通(本機兩個進程);`rca-app` image 大小不變。
- ffmpeg 峰值 RSS 量到的數字 ≤ workers.yaml 的 limit;`ruff` / `ty` / targeted 測試綠;`mkdocs --strict` 綠。
