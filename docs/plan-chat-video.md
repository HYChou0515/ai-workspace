# Plan：把一段對話紀錄做成影片（script 版先行，job / worker 版後接）

> **狀態:script 版已實作(PR #817,P1–P16:P1–P5 主體、P6 顯示工具、P7–P16 九輪 review 的修正),使用說明在 [chat-video.md](chat-video.md);P16–P18(job + route + 前端按鈕)列出形狀、未做。** 正文寫的是**現在的**做法;當初計畫和實作的差異在文末〈實作偏差〉。

## 需求（user 原話的整理）

1. 「輸入指令就生成動畫」——吃 Export 出來的 `.chat.json`(手改過也行),**不打 LLM、不起 API server**。
2. user 的那一輪,鏡頭要 **zoom 到輸入框**,逐字打、送出、拉遠。
3. 要能指定輸出的**長寬**。
4. 之後要變成**前端一顆按鈕**,由**獨立 job、專門的 worker pod** 產生影片——現在的切法要讓那一步只是「接上」,不是重寫。

## 已驗證的事實(proof,`tmp/zoomspike/`)

- 純 HTML/CSS 就能做 zoom:整頁容器 `translate(...) scale(k)` 一段 900 ms transition = 鏡頭推進;倍率要**封頂**讓整個輸入框進得了畫面(1280 寬時 1.8 → 1.55),而且要**推進 + 平移**把輸入框帶到畫面下三分之一——只設 `transform-origin` 會讓貼底的輸入框推進後仍然貼底、左邊被切、上面一片空。
- 錄影就是 viewport:Playwright `record_video_size = viewport` ⇒ `--width/--height` 直接是輸出像素,不縮放。
- 這台 Debian 11 只能跑 Playwright **1.49.x**(1.63 不支援 `debian11-x64`);Chromium 1148 已在 `~/.cache/ms-playwright`。
- 訊息要**貼底排**(最新的緊貼輸入框上方),推進時才看得到上一輪對話。

## 決定的做法

**一個 package 模組,三層乾淨切開;CLI 和未來的 job handler 都只是呼叫同一個純函式。**

```
src/workspace_app/chat_video/
  options.py    VideoOptions (msgspec.Struct,__post_init__ 驗證)  ← CLI 旗標 = 未來 job payload 的欄位 = 未來前端表單的欄位
  timeline.py   build_timeline(title, messages, options) -> Timeline   純函式,零重依賴;預估秒數、壓縮比;
                Timeline.wanted_files() / referenced_paths()(job 要先撈哪些檔)
  markdown.py   一次 markdown-it 解析,timeline(要讀哪些圖)和 player(畫哪些圖)共用;abs_path = 路徑的唯一拼法
  player.py     render_player_html(timeline, options, *, assets) -> str   自帶 CSS/JS 的單頁(player.html),無 CDN;
                decide_assets = 每條路徑畫不畫、為什麼;inline_assets = ASSETS 表(每檔一份 data URI)
  render.py     record(html, options, workdir, *, expected_ms) -> webm; encode(webm, fmt, out)   Playwright + ffmpeg,lazy import
  service.py    render_chat_video(*, title, messages, options, workdir, assets=None) -> dict[fmt, bytes]   ← 未來 job handler 用 to_thread 呼叫的就是它
  cli.py / __main__.py   python -m workspace_app.chat_video export.chat.json --files DIR -o demo.gif [--width …]
```

- **輸入是 `build_chat_export` 的形狀**(`{title, messages[]}`,`messages` 是 `Message` 的 `to_builtins`)。手改的 JSON 和「按鈕對著一段活的 chat」走**同一個**入口:未來的 route 只是把 `conv.messages` `to_builtins` 後丟給同一個函式,不另定格式。
- **重依賴走 extra**:`playwright` + ffmpeg 只在 `render.py` 裡 lazy import,放 `[project.optional-dependencies] chat-video`;API pod 的 image 不裝也能 import 這個 package(`timeline` / `player` 純 Python,CI 測得到);worker pod 的 image 才裝 extra + Chromium + CJK 字型。
- **同步函式**:`render_chat_video` 是 blocking(Playwright sync API + subprocess ffmpeg),未來 job handler 用 `asyncio.to_thread` 包——和 `Ingestor.index` 同一個慣例。
- **有上界**:`options.max_seconds`(預設 90)——timeline 先算預估秒數,超過就把所有延遲等比壓縮;一段 200 則的對話不會變成 20 分鐘的影片,worker 也不會被一個 job 卡死。Playwright 有 `timeout`,ffmpeg 有 `timeout`。
- **markdown**:`markdown-it-py`(rich 已帶進來,明列成直接依賴,同 `cryptography` 的前例);commonmark + 表格,HTML 關掉(`html=False`),連結 / autolink 關掉(URL 留成文字),圖片只從 `--files` 給的 bytes 畫、URL 永遠不抓;內容全部 escape——JSON 是手改的,不能讓它注入 script 進錄影頁。timeline 要讀哪些圖和頁面畫哪些圖是**同一次**解析(`markdown.py`),parity 測試守著。
- **輸出**:`fmt` ∈ `gif` / `mp4` / `webm`,可多選;GIF 走調色盤 + fps 上限;MP4 是 h264 yuv420p(投影片吃得下)。

## 每個 role 怎麼演

| `role` | 動畫 |
|---|---|
| `user` | 鏡頭推進輸入框 → focus 光圈 + 游標 → 逐字打(標點停頓稍長)→ Enter → 氣泡進對話串 → 拉遠 |
| `assistant` | 有 `reasoning` 先展開「思考」區塊逐字串流(灰、可摺),再逐字串流正文(markdown);`stopped_reason` 有值就加一個小標 |
| `tool` | 工具卡片:`tool_name` + `tool_args`(JSON 縮排,截到 `tool_output_chars`)→ 轉圈「執行中」停 `tool_pause_ms` → 展開 `content`(截到 `tool_output_chars`);結果尾端的 `[shown-files]` 宣告 → 卡片下接縮圖(宣告 `image/*` 且 bytes 在 ASSETS 表)或檔案卡;`show_file` 沒有卡片、檔案即畫面 |
| `error` | 紅色錯誤氣泡,`error_kind` 當小標 |
| `system` / `mention` / 其他 | 一行灰色置中提示 |

`author` 顯示在氣泡上方;「打字」動畫一律演成同一個人。

## 已鎖定的決策

| # | 決定 | 來源 |
|---|---|---|
| 1 | 不起 API server、不打 LLM;一條指令 | 「我要的是輸入指令 就生成動畫喔 不用放在api server」 |
| 2 | 仿真頁,不是真 app 畫面(像但非像素級) | plan 第一版,user 未反對;proof 已看過 |
| 3 | zoom 用 CSS transform 做,不後製 | proof |
| 4 | `--width/--height` = 輸出像素 | 「我還需要長寬大小的輸入 指定生成size」 |
| 5 | 核心是純函式 + msgspec options,為 job 版鋪路;script 版先行 | 「後面他會獨立成一個job由專門的worker pod處理 … 你可以先做script版」 |

## 知情取捨

- **不是真 UI。** 要像素級真的得起 app 錄(`/web-demo` 那條),那條不可能是「一條指令」也不可能在 worker pod 上跑(要 SPA + API)。
- **Playwright 釘 `1.49.x`** 是為了這台 Debian 11;image base 換掉後可以放寬,寫在 pyproject 註解。
- **worker image 要有 CJK 字型**(`fonts-noto-cjk`)不然中文是豆腐;這是 k8s 側的事(`reference_sandbox_host_ships_with_api`:prod 自維護 image),plan 點名、本 PR 不動 Dockerfile。
- **多個 `author` 只顯示名字**,不做多人打字。
- **不做**:主題切換(先深色)、頭像、user 訊息的附件、citation 渲染。(工具秀出的檔案與回答裡的 `![](path)` **有做**——P6,見〈實作偏差〉。)

## Phases(本 PR:P1–P5)

### Phase 1 — `VideoOptions` + `build_timeline`(純函式,TDD)
- `options.py`:`VideoOptions(width=1280, height=720, zoom=1.8, zoom_ms=900, type_ms=55, stream_ms=22, tool_pause_ms=1200, speed=1.0, max_seconds=90, chat_width=760, tool_output_chars=600, fmt=("gif",))`。
- `timeline.py`:messages → `list[Step]`(`TypeStep` / `StreamStep` / `ToolStep` / `ErrorStep` / `NoteStep`),每步帶自己的 `ms` 預估;`estimated_seconds()`;超過 `max_seconds` 時算出 `time_scale`。
- 測試(每條先紅):五種 role 各對到的步驟;`reasoning` 先於正文;tool 輸出截斷;預估秒數是加總;超上界時 `time_scale < 1` 且壓縮後 ≤ 上界;未知 role 變 `NoteStep` 不炸。

### Phase 2 — `render_player_html`(純函式,TDD)
- 單頁 HTML:CSS(深色、貼底排、zoom 容器、focus 光圈、工具卡片、思考區塊)+ JS 播放器(讀嵌入的 timeline JSON,照 `ms` 播;結束時 `document.body.dataset.done = "1"`)。
- markdown-it 渲染 assistant 正文;**所有** user / tool / error 文字 escape;timeline JSON 嵌入用 `</script` 安全序列化。
- 測試:`<script>` 出現在訊息裡不會變成標籤;CJK 與 emoji 原樣;`--width/--height` 進到 CSS 變數;`--zoom 1` 時 JS 不推進;產出的 HTML 不含 `http://` / `https://`(無 CDN)。

### Phase 3 — `record` / `encode` / `render_chat_video`
- `render.py`:Playwright sync,viewport = 尺寸,`record_video_size` = 尺寸,等 `done` 或逾時(原計畫 `max_seconds × 1.5 + 30`;實作是**壓縮後的播放時長** × 1.5 + 30 s,見第一輪);ffmpeg 轉 gif / mp4 / webm(subprocess,有 timeout,失敗把 stderr 尾巴丟進例外)。
- `service.py`:串三層,回 `dict[fmt, bytes]`;`workdir` 用完清掉。
- 測試:Playwright / ffmpeg 不在時的錯誤訊息是一句人話(不是 ImportError traceback);`encode` 用一支 1 秒的合成 webm 走 gif + mp4(標 `integration`,CI 不跑)。

### Phase 4 — CLI `python -m workspace_app.chat_video`
- 旗標對 `VideoOptions` 一對一;`--html` 只吐播放器;`-o` 副檔名決定 fmt(或 `--fmt` 多選);`--speed 2` 整體加速。
- `pyproject.toml`:`chat-video` extra(`playwright==1.49.*`)、`markdown-it-py` 明列。
- 測試:argparse → `VideoOptions` 的對應(含 `-o x.mp4` ⇒ fmt mp4)。

### Phase 5 — 文件 + 親眼驗收
- `docs/chat-video.md`(原計畫叫 `demo-chat-video.md`):安裝(`uv sync --extra chat-video` + `playwright install chromium`)、指令、旗標、JSON 手改的注意事項(`role` / `tool_name` / `tool_args`)、Debian 11 的版本釘。
- 用一份**真的** export(含 reasoning + tool + error)錄 1280×720 與 1920×1080 各一段(原計畫寫 1080×1080;正方形只留在文件的範例指令),抽 frame 看,GIF 傳給 user。

## 之後的形狀(P16–P18,本 PR 不做,寫下來讓接的人不用重想;原本編成 P6–P8,和後來的 commit 編號撞了,照 flat 規則改)

- **P16 job**:`ChatVideoPayload(item_id, chat_id, options: VideoOptions, user)`、`ChatVideoJob(Job[ChatVideoPayload])`、`ChatVideoRun`(status / progress / `video: Binary` / `error`)。`ChatVideoCoordinator`(同 `ImportCoordinator` 的形狀):`enqueue()` 由 route 呼叫;`_handle()` 讀 conversation → `to_builtins` → `build_timeline` → 對 `referenced_paths()` 逐一 `await files.read(item_id, path)` 組成 `assets`(`referenced_paths` 已不列爬出根的 `..` 路徑;façade 的 `abs_path` 不 jail `..`,所以 handler 讀之前仍要像 `cli.load_assets` 那樣拒絕解析到 workspace 外的路徑——第五輪點名;P12 起 `decide_assets` 自己也拒絕這種路徑,第二道鎖)→ `await asyncio.to_thread(render_chat_video, …, assets=assets)` → 存 Binary。`worker/__init__.py` 的 `_JOBTYPE_ATTR` 加 `"chat-video"`;`build_coordinators` 加進 bundle。
- **P17 route + 權限**:`POST /a/{slug}/items/{item_id}/chats/{chat_id}/video`(gate `read_chat`,同 export)回 run id;`GET …/video/{run_id}` 回狀態 / 下載。配額:一個 chat 同時只跑一個(partition_key = chat_id)。
- **P18 前端**:chat header 一顆「產生影片」按鈕 → 尺寸 / 格式的小表單(欄位 = `VideoOptions`)→ 進度 → 下載。
- **部署**:worker image 加 `chat-video` extra + `playwright install --with-deps chromium` + `fonts-noto-cjk`;`kubernetes/base/workers.yaml` 加 `chat-video` 一顆(prod 自維護,PR 要點名)。

## 驗收(P1–P5)

- `uv run python -m workspace_app.chat_video sample.chat.json -o out.gif --width 1920 --height 1080` 一條指令出檔;`ffprobe` 讀到的尺寸 = 指定尺寸。
- user 那一輪的 frame:輸入框整個在畫面內、光圈、游標、字正在打;拉遠後氣泡在對話串裡。
- 沒裝 extra 時 `import workspace_app.chat_video.timeline` 不炸;`ruff` / `ty` / targeted 測試綠;`mkdocs --strict` 綠。

## 實作偏差(寫回計畫,因為它會再被踩一次)

- **估算漏了播放器的固定停頓和每個字的瀏覽器開銷。** 第一版 timeline 只算「每字 × 毫秒」,實錄 29 秒對估算 13 秒。
  修法:所有固定停頓做成一份 `PACING` 表(Python 定義、嵌進頁面、JS 只讀它,測試釘住 JS 沒有自己的數字),
  再加一個量出來的 `CHAR_OVERHEAD_MS = 5`(timer 觸發 + DOM 插入 + 重繪)——它**不隨 `speed` 或壓縮縮小**,
  所以壓縮比只算在「要求的延遲」上,並有 0.05 的地板。修完 19.7 秒對 20.3 秒。
- **1080p 不是把 720p 的頁面放在大框裡。** plan 沒想到尺寸變大時版面要跟著放大;加了 `scale`
  (0 = 自動,`max(1, min(w/1280, h/720))`),用 CSS `zoom`。**`zoom` 會改 `translate` 的座標系**:
  鏡頭的平移要除以 scale,第一次 1080p 錄出來輸入框在左上角、左邊被切。
- **防注入測試第一版只被 `>` 的 escape 撐住。** 突變體「不 escape `<`」全綠——因為 `</script\u003e` 關不掉 script。
  測試改成斷言規則本身(嵌入的 JSON 不含任何 `<` / `>`),突變體才紅。
- **模板不能塞在 Python 字串裡**(E501 整片紅),改成 package data `player.html` 用 `importlib.resources` 讀。
- **reasoning 後 `content` 為空的訊息會多一個空的 answer step**;改成只有思考時不吐 answer。
- 沒做「`--zoom 1` 時 JS 不推進」的測試(那要真瀏覽器);JS 的 `if (!(k > 1)) return` 是那條規則,親眼看過 `--zoom 1` 的錄影。
- **第一輪 review(符合度 / 回歸 / 真實性三把鏡頭)抓到的,P7 修掉:**
  - `tool_args` **沒截斷**(plan 第 46 行寫了「截斷」):一個 `write_file` 就把卡片撐成 4,000 px、標頭在畫面外 3,500 px。
    現在參數 JSON 也吃 `tool_output_chars`。
  - `record()` **整個函式沒有任何測試執行到**、`chat_video/__main__.py` 沒進 coverage omit——full-suite 100% gate 會紅。
    補了假 `playwright.sync_api` 的單元測試(Chromium 沒裝的句子、逾時、成功路徑)+ 一條真 Chromium 錄 400×300 並
    ffprobe 回 400,300 的 integration 測試;套件 statements + branches 100%。
  - 錄影 deadline 用的是**未壓縮**估算(40 則 × 500 字、`max_seconds=1` ⇒ 31 分鐘的 deadline);plan 寫的是
    `max_seconds × 1.5 + 30`。改成 `playback_ms`(壓縮後 + 開銷)× 1.5 + 30 s。CLI 印的也改成 `playback_ms`。
  - 上限是**軟的**:每字瀏覽器開銷壓不掉(20k 字無論 `max_seconds` 都 ~110 s);文件與 options docstring 明講。
  - 「每個延遲都走 `t()`」的守衛只是 `count("PACING.") >= 8`,而 script 有 10 處——兩個字面量都放得過。改成
    「每個 `sleep(` 都是 `sleep(t(`、`t(` 後面不能是數字」;突變體 `sleep(t(600))` 現在紅。
  - Chromium 沒裝(`playwright install` 沒跑)是整段 traceback、頁面逾時是 traceback 且沒有檔案、ffmpeg 逾時是
    traceback、ffmpeg 缺少要等**錄完**才發現:四條都改成一句話,ffmpeg 在錄之前就查。
  - 手改 JSON 的常見錯誤全是 traceback,而 repo 已經有 `kb.chat_export.parse_chat_export`(KB 上傳用):CLI 改用它。
  - `VideoOptions` 零驗證(`--speed 0` ZeroDivisionError、`fmt=["exe"]` 錄完才被 ffmpeg 拒絕):加 `__post_init__`,
    三個讀者(旗標 / job payload 的 msgspec decode / 表單)同一套。
  - `stopped_reason` 小標沒做(plan 第 45 行);補上。
  - 句子:「指令會印出預估秒數」錄影路徑其實沒印;「3% 內」量出來預設速度是 +4.5%、短片 +14%、壓縮片 −68%(印的是
    未壓縮數);Chromium「約 150 MB」實際 ~860 MB;`CHAR_OVERHEAD_MS` 註解的「600 字」實際 485、「speed 與壓縮都縮不了」
    量出來會縮(6.5 → 4.0 → 2.1 ms/字);`+30_000` 不是 floor 是 slack;「Chromium is the only renderer」被 `--html` 自己
    否定;`test_service` 的 scratch 斷言查錯路徑(永遠綠);plan 的 `-> bytes`(實際 `dict[fmt, bytes]`)、
    `estimated_seconds()`(實際 `estimated_ms`)、`docs/demo-chat-video.md`(實際 `chat-video.md`)、「可摺」(沒做)、
    「照 `ms` 播」(script 不讀 `ms`)。全部改掉或記在這裡。
- **第二輪 review(只看 P6+P7 的 diff)抓到的,P8 修掉:**
  - **HIGH:`referenced_paths()` 用自寫 regex 找 `![](path)`,播放器用 markdown-it 正規化過的 href 找 assets——16 個輸入
    9 個不一致**,中文檔名的圖被讀進來但永遠不畫、也不報。這就是 CLAUDE.md 那條「A 讀檔要和 B 一樣 = parity test 以 B 為
    oracle,不是手寫兩份」。修法:抽 `markdown.py`,兩邊都走 markdown-it 的 token stream + `unquote`;`test_markdown_parity.py`
    14 個 case 以「頁面畫的 = timeline 要的」為 oracle,把 regex 放回去 6 個紅。
  - 圖片每個引用處各嵌一份 data URI:20 次 `show_file` 同一張 4 MB 圖 = 107 MB 頁面。改成頁面一張 `ASSETS` 表、每檔一次,
    加 `max_assets_total_bytes`(24 MB,依出現順序花);CLI 讀檔前先看大小。
  - 「沒給 bytes 就變檔案卡」只對宣告的檔案成立,`![]()` 是剩 alt 文字——文件與 CLI 的 note 改對,原本的測試釘的是錯句。
  - 「上限只軟在一項」是錯的:壓縮的 5% 地板是第二項。三處句子改掉;deadline 逾時的建議句也改(降 `--max-seconds` 在那個
    regime 沒用)。
  - `_image` docstring 把「URL 不抓」寫成前端的規則——前端會抓,不抓是影片自己的。
  - 數字:文件的 `--speed 1.5` 19.7→20.2 是舊範例(現在的範例是 27.1→28.1);`CHAR_OVERHEAD_MS` 註解 485 字是舊範例
    (現在 593);「40 則 × 500 字、1 s 上限 = 31 分鐘 deadline」算出來是 23.9 分鐘(現在 4.0)。
  - `load_assets`:NUL 字元 / 5000 字路徑 / 沒權限 → traceback;`size: NaN/Infinity` → traceback;junk 在 `{` 前面比前端寬鬆;
    RIFF 一律當 webp(WAV 也是 RIFF);ms 類 option 沒上限(2³¹ 溢位變立即逾時);假 playwright 的 `video.path()` 在
    `context.close()` 前就存在(真的不會);`test_service` 又多一個 `or True` 空斷言;`del d["args"]` 沒有守衛。全部修。
  - 沒修、記下:三樣工具都缺時要三次來回才問完(每次一句話,沒有白錄);`chat_width > width` 由 CSS `min()` 兜住。
- **第三輪 review(只看 P8 的 diff)抓到的,P9 修掉——沒有 HIGH。我把 P9 叫成「小修不換機制」就停手,錯:它換了三個機制
  (note 的來源、一次 walk、路徑的 key),依規則要再一輪(第四輪,下面)。**
  - 一個真缺陷:`![![y](q.png)](p.png)`(圖的 alt 裡再放一張圖)——markdown-it 會把 alt 也解析成 token,timeline 的
    walk 走進去把 `q.png` 列進要讀的清單,但頁面把 alt 壓平成文字、永遠不畫它。就是第二輪那一類(讀了不畫)剩下的一個
    形狀。修法:走到 image token 就 `continue`,不進 alt。parity case 加 4 個(18 個),砍掉 `continue` 恰好紅那兩條。
  - parity 測試的 docstring 說「以頁面為 oracle」,但頁面只能被餵 timeline 列出的路徑,所以它只抓得到「列太多」,抓不到
    「列太少」——那半邊靠的是手寫的 `EXPECTED` 規格。docstring 改成說實話:兩個守衛各守一個方向。
  - CLI 的「哪個路徑沒畫」只算「`--files` 沒讀到」一種;讀到了但不是圖(SVG 圖表)、讀到了但超出總預算,都靜默。
    改成從頁面自己的判斷(`decide_assets`,三個理由)來報——判準裝在值被算出的地方,CLI 只轉述。
  - `_wanted()`(player)是 `Timeline.referenced_paths()` 的手抄雙胞胎。收成 `Timeline.wanted_files()` 一次 walk,
    `referenced_paths` 是它的去重視圖、頁面的 ASSETS 表也從它來。
  - 路徑沒正規化:`plots/a.png`、`./plots/a.png`、`plots//a.png` 是三個 key ⇒ 讀三次、嵌三份。`abs_path` 改 `posixpath.normpath`。
  - 「超出預算變卡片」的實際行為是 first-fit 不是前綴(塞不下的跳過、後面小的照嵌);而且預算算原始 bytes,base64 後頁面
    約大三分之一。文件與 docstring 照實寫,加一條釘住 first-fit 的測試。
  - 3.12 的 `Path.resolve()` 撞到 symlink loop 丟 `RuntimeError`,不是 `OSError` → traceback。接住。
  - `[shown-files]` 後面 10 萬層 `[` → `RecursionError` traceback;前端是 `SyntaxError` = 沒有宣告。接住,同一句意思。
  - `type_ms` / `stream_ms` 的上限沒被測試釘住;`speed` 只有 `> 0`,`0.001` 會讓每個延遲放大千倍、壓縮救不回來。
    釘住;`speed` 改 0.1–100。timer 溢位那句「立即觸發」只對一半(也可能是幾週後),改寫。
  - 測試 95 → 125 條(含 2 條 integration;`pytest --collect-only` 數的):parity 14 → 36(18 個 case × 兩個方向)、
    options +4、player +2、CLI +2、timeline 同數(一條改名加深)。每個修法一個突變體,十個都恰好紅在對應的測試;
    對照組(砍掉 tool files 的 walk)紅 5。
- **第四輪 review(只看 P9 的 diff,三把鏡頭)抓到的,P10 修掉——最嚴重的一條(符合度鏡頭判 HIGH、回歸鏡頭判 MED)是 P9 自己的回歸。這輪之後 user 說「review 太多次是
  bad smell,要改 fix methodology」:第三輪 9 條有 7 條是 P8 修法的殘缺、第四輪的 HIGH 是 P9 修法的——每輪都在修被回報的
  那個實例,下一輪抓同一類的下一個形狀。P10 改成先把機制的輸入空間列成表再修:**
  - **P9 回歸:`decide_assets` 讓第一個引用的判定黏在路徑上。** 回答先寫 `![](plots/chart.svg)`(沒宣告、SVG sniff
    不出 → 「不是圖」),下一輪 `show_file` 宣告 `image/svg+xml`——P9 兩處都變卡片、note 還說「不是圖」;P8 會畫。
    表:「一條路徑 × N 個引用(回答 `![]()` / 宣告 image/* / 宣告非圖)× 一份 bytes」→ 每個引用畫什麼、note 說什麼。
    判定是**路徑**的性質:先掃所有引用取宣告的 image mime,再判一次;順序無關。四列 parametrize(回答先 / 宣告先 /
    佔位後圖 / 兩個 image mime),砍掉前置掃描恰好紅「回答先」那列。
  - 同一張表的兩列 reviewer 各點到一半:宣告 `text/csv` 的檔**本來就是卡片**(FE 的 `isInlineImage` 只看宣告的 mime),
    P6 起 `wanted_files` 把它列進要讀的檔(白讀),P9 再對它印假的「will not be drawn」;反過來,同一路徑先宣告 `image/png`
    再宣告 `text/csv`,FE 是圖 + 卡片、播放器(P8、P9 都)兩張圖。修法:`wanted_files` 只列 `image/*` 宣告與回答的 `![]()`;
    `player.html` 的 `shownFiles` 依宣告自己的 mime 決定畫圖或卡片——真 Chromium 的 integration 測試數 DOM(2 圖 2 卡)。
  - `normpath` 把 `/../secret.png` 折回 `DIR/secret.png` 並畫出來;FE 的 URL 會解析到 `/files/` 之上、破圖不畫。改成爬出根
    的 `..` 保留原字(jail 拒、note 說「not under DIR」),其餘照常正規化;`abs_path` 的 17 列拼法表在 `test_markdown.py`。
    推前自審抓到第一版(在 sentinel 目錄 `/w` 下正規化再看前綴)的碰撞:`/../w` 正規化成 `/w` 等於 sentinel 本身——先加
    兩列(紅),再改成逐段數深度、低於根就保留原字。
  - 修實例沒修類,兩個:export **檔案本身** 10 萬層 `[` 仍 traceback(第三輪只修了宣告;`parse_chat_export` 只接
    `JSONDecodeError`,KB 上傳同一個入口)→ 接 `RecursionError` 成同一句「invalid JSON」;`--files` 本身指到 symlink loop
    仍 traceback(`files_dir.resolve()` 在 try 外)→ 一句話 exit 2。
  - `size: 10**400`:瀏覽器是 `Infinity`(number,FE 留著畫卡片),Python 是 `math.isfinite` 轉不成 float 的 int →
    `OverflowError`。int 一律留。
  - 文字:`--help` 兩句還是舊的(「spent in reading order」「is a file card」);parity docstring「each for one direction」
    ——`EXPECTED` 是 list 相等,兩個方向都守,頁面那條不多餘的真正理由是「手寫的表可能自己錯」;`speed` 註解的 11 h /
    33 min 是零 overhead 的理想值(範例算出來 10.0 h / 30.2 min);commit message「join the eighteen」(是 14 + 4,plan
    寫對了,commit 不改);note 的判定為了三句理由把 24 MB base64 編了一次、頁面再編一次 → `decide_assets` 只回判定,
    `inline_assets` 才編碼。
  - 沒修、記下:宣告路徑 `.` 或 `///` 折成 `/`,卡片檔名是空字串(手改才會有);`NaN`/`Infinity` 字面量 FE 整段宣告作廢、
    播放器只跳過那一筆(第二輪就決定的)。
  - 測試 `tests/chat_video` 125 → 156 條(`--collect-only` 數的;3 條 integration):`test_markdown.py` 17 列、parity +4
    (2 列 × 2 方向)、player +6(4 列 parametrize + 1 + 1 integration)、timeline +1、CLI +3;`tests/kb/test_chat_export.py` +1。
- **第五輪 review(單一問題:P10 的表有沒有漏列)抓到的,P11 修掉——最嚴重的一條又是 P10 自己的:**
  - **P10 的規則本身錯了**:「宣告的 mime 蓋過 bytes」——回答先 `![](x.png)`、後來 `show_file` 宣告 `image/svg+xml` 但 bytes 是
    PNG,P10 產出 `data:image/svg+xml;base64,<PNG>`;Chromium 對 raster 會 sniff、對 SVG 只認 mime → 破圖兩處、判定是「圖」所以沒
    note;P9 會畫(reviewer 用真 Chromium 量的)。改成 **bytes 先 sniff,認不出才用宣告**(SVG 就是認不出的那種)。這次的表是
    「bytes(png / svg 文字 / 一般文字 / 空 / 沒有)× 宣告(無 / png / svg / jpeg 不符 / 逗號 / 帶參數)」13 列直接對 `decide_assets`
    的 verdict,兩個引用順序都比;砍掉 sniff-first 恰好紅 png×svg、png×jpeg 兩列。
  - 同一張表的兩列:0 byte 的宣告圖是 `data:image/png;base64,` 破圖沒 note(聊天視窗也是破圖,但頁面說「never a broken
    <img>」)→ 「不是圖」;宣告 mime 含 `,` 會截斷 data URL 的 header → 「不是圖」(`;` 帶參數照畫)。
  - 同類的第三扇門:`tool_args` 巢狀 1,500 層——檔案能 load(C 解碼器允許),`json.dumps(indent=2)` 走純 Python 編碼器就
    `RecursionError`。接住,卡片寫「(arguments nested too deep to show)」。
  - `referenced_paths()`(job 要預撈的清單)原本含爬出根的 `..` 路徑;façade 的 `abs_path` 不 jail,未來 job 會讀到 workspace
    外。清單不列它們(仍在 `wanted_files`,所以 note 照說「not under DIR」);P12 的 handler 仍要自己 jail(上面寫進去了;P12 讓頁面自己也拒絕)。
  - 文字:`size` 巨大整數那條的註解與 docstring 寫成 `1e400`,但浮點寫法 `1e400` 在 Python 是 `inf`、仍被丟(FE 留 `Infinity`
    畫卡片)——改成說「400 位數的整數」並把 `1e400` 記進差異;JS 那條規則的單元測試是原文守衛,行為只有 integration
    (真 Chromium)守著,CI 不跑 integration——docstring 有寫明,記下。
  - 沒修、記下(不在這個 PR):聊天視窗自己對中文 / 含空白檔名的 `![]()` 是破圖——`workspaceUrl` 把 react-markdown 已
    percent-encode 的 `src` 直接交給 `encodePath`,每段再 `encodeURIComponent` 一次(double-encode),後端找不到檔;播放器畫得出
    是因為 `markdown.py` 有 `unquote`。`docs/chat-video.md` 那句「都認得」說的是播放器。FE 的 bug,另開票。
  - 測試 `tests/chat_video` 156 → 171 條(`--collect-only` 數的):player +13 列 +1、timeline +1;五個修法各一個突變體,每個恰好紅在
    自己的列;對照組紅 37。
- **第六輪 review(單一問題:P11 的 13 列完整嗎)抓到的,P12 修掉——P11 零回歸,但表又是「加了 reviewer 那一列」而不是那一類:**
  - 「逗號 mime」是「壞掉的 mime 字串」這一**類**的一員——reviewer 用真 Chromium 跑出 17 種(換行、引號、`#`、`?`、`%`、`image/`、
    `image/*`…)全部「判定是圖、破圖、沒 note」;兩次宣告不同 image mime 時 `setdefault` 是 first-wins,順序相依(第四輪禁掉的性質;
    檔案重生後第二次 `show_file` 的 mime 才描述現在的 bytes);sniff 表少了 Chromium 會畫的 BMP / ICO / AVIF、TIFF(Chromium 沒解碼器)
    被宣告成 `image/tiff` 就破圖。
  - 修法不是白名單也不是 last-wins,是讓這兩類**由構造消失**:data URI 的 mime **只從 bytes 來**——raster 以簽名(PNG / JPEG / GIF /
    WebP / BMP / ICO / AVIF = Chromium 解碼的全集;TIFF 不在)、SVG 以文字認(P12 第一版是「開頭是序言/註解/`<svg` 且前 1 KB 有
    `<svg`」,推完自己看到「`<!-- -->` 開頭、內文含 `<svg` 的 HTML」會被當 SVG;P13 改成真的掃過 BOM / 空白 / `<?…?>` / `<!DOCTYPE>` /
    註解,**第一個元素必須是 `<svg`**,`<svgfoo>` 也不算——4 列先紅再修)——認不出的 bytes 一律不畫(卡片 / alt + note「不是圖」;聊天視窗對這種是破圖,頁面的規則「never a broken <img>」
    較強)。宣告的 mime **永遠不進 URL**,只剩「這次宣告畫圖還是卡片」(`isInlineImage`)一個用途——所以壞字串沒有可壞的地方、兩次宣告沒
    有可爭的順序。副作用:回答裡的 `![](x.svg)` 現在畫得出來(和聊天視窗一致;第二輪釘的「不畫」改掉——`<img>` 裡的 SVG 不跑 script、
    不抓外部資源)。
  - 表改成「bytes 15 種 × 宣告 12 種(含那 17 種裡的代表)× 兩個順序」= 180 格,斷言宣告軸不動任何一格;真 Chromium 的 integration 測試
    用 Pillow 現產 8 種格式經 `inline_assets` 進頁面、每張 `naturalWidth > 0`——「sniff 表 = Chromium 畫得出的全集」由執行釘住。
    `decide_assets` 也拒絕爬出根的路徑(job 版的第二道鎖);parity 的頁面守衛改餵 `referenced_paths()`(job 會交的那份)。
  - 測試 `tests/chat_video` 171 → 389 條(`--collect-only` 數的):−13 舊列 +(15+4)×12 格 +3;五個突變體(宣告漏進 URI / 不 sniff SVG /
    不 sniff BMP·ICO / 不 sniff AVIF / 頁面不拒 `..`)各紅自己的格,含真 Chromium 那條逐格式紅;對照組(簽名全不比)紅 88。
- **第七輪 review(單一問題:「只看 bytes」完整且健全嗎)抓到的,P14 修掉——1 HIGH,是 P13 自己的:**
  - **我手寫的 XML 序言掃描器**在 `<!DOCTYPE svg […]>` 的內部子集裡第一個 `>` 就停——Adobe Illustrator 存檔的標準檔頭、matplotlib 自帶的
    `hand.svg` 都被判「不是圖」、note 說假話;reviewer 掃本機 30,000 個真 SVG 恰好 2 個被拒,都是這形。同一支掃描器還漏 UTF-16+BOM、
    `<svg:svg>` 前綴根、4 KB 以上的序言,反過來把沒 `xmlns` 的 `<svg>`(Chromium 的 SVGImage 要求根在 SVG namespace)當圖、破圖沒 note。
    **根因是自己發明 parser 而 stdlib 的 expat 就在那裡**——記憶裡「照抄成熟做法別自己發明」「用 framework 內建」兩條都有,又犯。
    修法:問 expat 第一個元素的 namespace + 名字是不是 `http://www.w3.org/2000/svg svg`(handler 裡 raise 停在第一個元素;不設外部實體
    handler、不展開內容,子集裡的 entity bomb 碰不到)。BOM / UTF-16 / PI / 帶子集的 doctype / 任意長的註解 / 前綴根 / 沒 xmlns / XHTML 根
    全由真 parser 決定。
  - 簽名表補 CUR(`\x00\x00\x02\x00`,和 ICO 同容器)、OS/2 `BA`;AVIF 改照 ISOBMFF 讀 `ftyp` box(size 0 = 到檔尾、size 1 = 64-bit
    largesize),`avif` 在 major 或 compatible brands 任一就算(libavif 的 peek 規則);HEIC 不算(Chromium 不解碼)。
  - reviewer 在真 Chromium 上驗了第二輪釘住又在 P12 拿掉的那句:`<img>` 裡的 SVG 帶 `onload` / `<script>` + fetch / `<image href=http…>` /
    `<use>` / `@import` / `<foreignObject><iframe>`,`done` 照設、兩張都有 `naturalWidth`、HTTP listener 零命中、無新 frame。
  - 文字:P13 的 commit 說「4 列裡 2 列在 P12 上紅」,實際 3 列(「註解沒收尾」在 P12 也紅);記下。
  - 沒修、記下(和 raster 共有、任何前綴 sniff 都看不到):內文壞掉的 SVG(`&nbsp;`、截斷)會判「圖」而破圖——PNG 截斷也是同樣的洞;
    SVGZ 在 data URI 裡 Chromium 不解、聊天視窗的檔案路由也丟掉 gzip encoding,兩邊一致地壞,是 FE/route 的另一張票。
  - 表:bytes 19 → 30 種(+doctype 子集 / UTF-16 / 前綴根 / 5 KB 註解 / 沒 xmlns / XHTML 根 / CUR / BA / AVIF 64-bit / AVIF 相容 brand /
    HEIC)× 12 × 2,另加 entity bomb 兩格(根屬性裡 → expat 放大上限擋下、量到 0.4 s;子元素裡 → 碰不到,所以獨立於 12 寬的表);`tests/chat_video` 389 → 523 條(`--collect-only` 數的,+11×12 +2)。六個突變體(namespace 不看 / 回到前綴檢查 / CUR /
    BA / AVIF 只看 major / 64-bit size)各紅自己的列,回到前綴檢查紅 96 格;對照組 112。
    七個修法各一個突變體,每個恰好紅在自己的測試(`abs_path` 那個紅 8 條含 CLI 端到端);對照組(`wanted_files` 空)紅 36。
- **第八輪 review(單一問題:expat 的第一個元素 vs Chromium 的 SVGImage)抓到的,P15 修掉——P14 的 expat 又帶來自己的一類:**
  - `encoding="Shift_JIS"` 讓 pyexpat 丟 `ValueError`(不是 `ExpatError`),一張秀出來的檔讓整段渲染死掉、沒頁面;整份 4 MB 餵進
    parser,3.9 MB 的註解讓 libexpat 的放大上限(100×、8 MiB 後才生效)吃到 1.2 GB 記憶體;pyexpat 1 MiB 分塊 + reparse deferral 讓
    一個 token 超過 1 MiB 的合法 SVG 被判「沒元素」;TGA type 2 和 CUR 同檔頭;`BA` 撞到 "BATCH…" 開頭的文字檔;ATTLIST 預設值二次方
    (4 MB 要 9 s)。reviewer 337 個輸入逐一過 sniffer + 真 Chromium。
  - **停下來看第五~八輪的共同點:我們在替 Chromium 判斷 bytes 是什麼,每換一種判法就換一類分歧。** 聊天視窗從不看 bytes——檔案路由用
    副檔名給 Content-Type(`guess_type`,猜不到就看能不能 UTF-8 decode),Chromium 自己決定(raster 不管 mime 都 sniff、SVG 只認 mime)。
    P15:把路由那五行抽成 `files/media_type.py:media_type_for`,**路由和播放器呼叫同一個函式**(`test_read_file_serves_the_shared_media_type_rule`
    用 monkeypatch 釘住路由真的在呼叫它;兩邊各做一個「手抄一份」的突變體,各紅);播放器不再 sniff、不再 parse——同 bytes、同型別、同一個
    Chromium ⇒ drawn == drawn 由構造成立。頁面自己只剩兩個理由:沒拿到 bytes、超預算;「不是圖」這個理由連同 `_sniff_image` /
    `_first_element_is_svg` / expat / 簽名表一起刪掉。`.txt` 被 `![]()` 指到會是破圖(聊天視窗也是),不再是 alt 文字——影片的規格是
    「聊天視窗畫的」,不是「比聊天視窗好看」。
  - 表改成「檔名 × bytes 13 列 × 宣告 5 種 × 兩個順序」,oracle 就是共用函式本身;真 Chromium 的 integration 測試把 13 種各送兩次——本機
    HTTP server 用 `media_type_for` 當 Content-Type(聊天視窗的投遞)和頁面的 data URI——`naturalWidth` 逐一相等(畫或破一致)。
    這台 mime DB 對 `.webp` 回 `None`(路由給 octet-stream、Chromium 照樣 sniff 畫出),是這個設計才自然對的一列。
  - 記下:mime DB 隨機器(`/etc/mime.types`)不同,worker pod 和 API pod 的 image 若不同,`.webp` 之類可能一邊 image/webp 一邊
    octet-stream——但 Chromium 對 raster 兩邊都畫,只有 SVG 依賴 `.svg`(Python 內建表有)。P16 的 handler 仍要 jail `..`(上面寫了)。
  - 測試 `tests/chat_video` 523 → 227 條(`--collect-only` 數的:−30×12 −2 −1 −1 +13×5 +3);`tests/api/test_messages.py` +1;四個突變體
    (播放器手抄規則 / 路由手抄規則 / 頁面不拒 `..` / 不看預算)各紅自己的測試;對照組(全當 image/png)紅 52。
- **第九輪 review(單一問題:「路由的型別 + Chromium 決定」的 parity 有沒有破口)——沒有 HIGH、沒有機制回歸,P16 只補測試與文字:**
  - reviewer 用 105 種 bytes × 兩種投遞(本機 HTTP 帶 Starlette 真正送出的 header、頁面的 data URI)在同一個 Chromium 上比 `naturalWidth`:
    **0 分歧**;830 組(檔名 × bytes)比路由的舊五行 vs `media_type_for`:0 分歧;126 種路徑拼法比路由與播放器的正規化:113 種一致,
    13 種不一致——全是**聊天視窗自己的 double-encode bug**(第七輪記過的那條,這次用真 uvicorn 驗證了:react-markdown 給的 `src` 已
    percent-encode,`encodePath` 再編一次,後端找的是字面 `%E5%9C%96.png`;Starlette 的 `TestClient` 會解碼兩次所以**驗不到**,要走
    uvicorn)。從真入口跑 12 個檔的 workspace:P15 對聊天視窗的標準 11/12、P14 10/12(且 P14 遇到 Shift_JIS 會整段死掉)。
  - **我 P15 的 commit 有一句假的**:「播放器手抄規則的突變體會紅」——我跑的是「另一條規則」(`guess_type or octet-stream`),逐字抄那五行
    227 條全綠,因為表的 oracle 就是那個函式。補一個和路由同形的 monkeypatch 釘子(`test_the_page_calls_the_route_s_rule_not_a_copy_of_it`),
    逐字抄本現在恰好只紅這一條。
  - 記下(設計的後果,不改):不看 bytes 就分不出非圖片,`![](report.pdf)` 會把 PDF 的 bytes 帶進頁面(破圖,聊天視窗也是)並佔圖片預算——
    要 ≥20 MB 的非圖片排在圖前面才會擠掉一張真圖;任何用型別跳過的規則都會把「PNG 存成 .txt」(聊天視窗畫得出)變成卡片。
  - 文字:`Verdict` docstring「三句」剩兩句;parity 測試的 HTTP 端原本送 `media_type_for` 的原字串,Starlette 對 text/* 會加 charset——改成
    送路由真正建出的 header。
  - 記下(不在這個 PR):影片用 Playwright 附的 Chromium 錄,聊天視窗在 user 自己的瀏覽器——AVIF / HEIC / 多位元編碼的 SVG 隨版本與
    引擎不同;prod nginx 對 `%25…` 的路徑會不會先解碼一次,我看不到設定。

- **plan 漏了「顯示工具」**(user 問「show file 能夠顯示嗎」才補)。前端把檔案放到人面前有三條路——`show_file`
  (沒有卡片,檔案即畫面)、任何工具結果尾端的 `[shown-files]` 宣告、回答裡的 `![](路徑)`——bytes 都不在 export 裡。
  加了 `assets: Mapping[path, bytes]` 接縫(CLI `--files DIR` 讀;未來 job 用 `Timeline.referenced_paths()` 先撈再進
  thread),圖內嵌成 data URI(頁面仍不抓網路)、非圖 / 沒 bytes / 超過 `max_asset_bytes` 是檔案卡;`![](https://…)`
  永遠不載入。`SHOWN_FILES_MARKER` 從 `agent.shown_files` 匯入(共用,不抄)。突變體「URL 直接當 src」讓兩條測試紅。

