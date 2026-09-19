# 把一段對話做成影片

把 Export 按鈕下載的 `.chat.json` 變成一段動畫:user 的訊息被逐字打進輸入框(鏡頭推進)、送出、
AI 的思考與回答逐字串流、工具呼叫變成卡片。**不打 LLM、不起 API server**——一條指令,本機跑完。

設計與之後的 job / worker 版形狀見 [plan-chat-video.md](plan-chat-video.md)。

## 安裝(一次)

```bash
uv sync --extra chat-video          # Playwright(Python 套件)
uv run playwright install chromium  # Chromium + headless shell,約 860 MB,放在 ~/.cache/ms-playwright
which ffmpeg                        # 沒有就 apt install ffmpeg
```

三樣少了任何一樣(Python 套件、Chromium、ffmpeg),指令都用一句話告訴你少了什麼、exit 3,不會噴 traceback;
ffmpeg 在**錄影之前**就檢查,不會錄完才說。

!!! note "Debian 11"
    Playwright 釘在 `1.49.x`:更新的版本在 Debian 11 上 `playwright install` 會拒絕
    (`does not support … on debian11-x64`)。這是開發機的限制,不是產品的;image base 換了就放寬。

## 指令

```bash
uv run python -m workspace_app.chat_video my.chat.json -o demo.gif
```

| 旗標 | 預設 | 意思 |
|---|---|---|
| `-o / --out` | `<source>.gif` | 輸出檔;**副檔名決定格式**(`gif` / `mp4` / `webm`) |
| `--fmt gif\|mp4\|webm` | — | 多寫一種格式(可重複);檔名跟 `-o` 同名換副檔名 |
| `--html preview.html` | — | 只吐播放器頁面、不錄影;用瀏覽器開來看,改 JSON 再錄 |
| `--files DIR` | — | workspace 資料夾:`show_file` / 畫圖工具秀出的檔案、回答裡 `![](路徑)` 的圖,從這裡讀進頁面(見下) |
| `--width / --height` | 1280 / 720 | 輸出像素。**錄影就是 viewport**,不縮放 |
| `--chat-width` | 760 | 聊天欄寬度(CSS px,放大前) |
| `--scale` | 0 = 自動 | 整個 UI 的放大倍率;自動 = 畫面相對 1280×720、不小於 1(1080p 是 1.5、4K 是 3) |
| `--zoom` | 1.8 | user 打字時鏡頭推進的倍率;`1` = 不推。會自動封頂讓整個輸入框進得了畫面 |
| `--zoom-ms` | 900 | 推進 / 拉遠的時長 |
| `--type-speed` | 55 | 打字:每個字幾毫秒(標點停 3 倍) |
| `--stream-speed` | 22 | 串流:每個字幾毫秒 |
| `--tool-pause` | 1200 | 工具卡片轉圈多久 |
| `--speed` | 1.0 | 整體倍速;`2` = 快一倍;範圍 0.1–100 |
| `--max-seconds` | 90 | 影片上限。超過的話**所有延遲等比壓縮**,不丟訊息、不截尾。軟上限:瀏覽器每個字的固定開銷壓不掉、壓縮也只壓到 5%,幾萬字的對話還是會超過(指令會印出實際會播多久) |
| `--tool-output-chars` | 600 | 工具輸出**和參數**超過就截斷加 `…`(`write_file` 的整個檔案內容不會撐爆卡片) |
| `--max-asset-bytes` | 4000000 | 一張圖超過這個大小就不畫(宣告的變檔案卡、`![]()` 剩 alt 文字;而且根本不讀進來) |
| `--max-assets-total-bytes` | 24000000 | 整頁內嵌圖片的總預算(原始 bytes;base64 後頁面約大三分之一);同一張圖不論秀幾次只嵌一次;依出現順序 first-fit——塞不下剩餘預算的那張不畫,後面塞得下的小圖照畫 |

常用組合:

```bash
# 投影片:1080p 的 MP4,順便一份 GIF
uv run python -m workspace_app.chat_video my.chat.json -o demo.mp4 --fmt gif --width 1920 --height 1080

# 正方形、快一點、不推鏡頭
uv run python -m workspace_app.chat_video my.chat.json -o sq.gif --width 1080 --height 1080 --speed 1.5 --zoom 1

# 先看再錄
uv run python -m workspace_app.chat_video my.chat.json --html preview.html && xdg-open preview.html

# 對話裡有 show_file / 畫圖:把 workspace 一起給它
uv run python -m workspace_app.chat_video my.chat.json --files ./my-workspace -o demo.mp4
```

指令會印出「會播多久」(`will play 39.1s`;有壓縮時連原本要多久一起印)。實錄比它長約 3–6%(附的範例:預設速度
39.1 s 估 → 41.2 s 實錄、`--speed 1.5` 27.1 → 28.1);很短的片子比例更高(4.5 → 5.1 s);被壓縮的片子反而略短
(`--max-seconds 10`:10.0 估 → 8.8 s 實錄)。每個字的瀏覽器開銷是一個常數近似(`CHAR_OVERHEAD_MS`),故意往多估。

錯的輸入(壞 JSON、少 `title`、第 N 則不是物件、`content` 不是字串)是一句話 + exit 2——和 KB 上傳同一個驗證器;
錯的旗標值(`--speed 0`、`--zoom 0.5`)也是一句話 + exit 2。錄影途中失敗(頁面沒在期限內播完、ffmpeg 失敗)exit 4。

## JSON 怎麼改

檔案就是 Export 的格式:`{"title": "...", "messages": [...]}`,每則訊息是 `Message` 的欄位。
手改時只有這幾個欄位會影響畫面:

| `role` | 用到的欄位 | 畫面 |
|---|---|---|
| `user` | `content`、`author` | 鏡頭推進輸入框,逐字打,送出 |
| `assistant` | `content`(markdown)、`reasoning`、`author`、`stopped_reason` | 有 `reasoning` 先串流灰色「思考」區塊,再串流正文;`content` 空就只有思考;`stopped_reason` 有值就在正文下加一行紅色小標 |
| `tool` | `tool_name`、`tool_args`、`content`(= 輸出) | 工具卡片:名稱 + 參數 → 轉圈 → 輸出;結果尾端有 `[shown-files]` 宣告的,卡片下面接檔案(見下) |
| `tool` = `show_file` | 同上 | **沒有卡片**,檔案本身就是畫面(和聊天視窗一樣) |
| `error` | `content`、`error_kind` | 紅色氣泡 |
| 其他(`system`、`mention`、…) | `content` | 一行灰色置中提示 |

- markdown 支援標題 / 粗體 / 清單 / 行內碼 / 程式碼區塊 / 表格;**連結變成純文字**,圖片只在指向 workspace 路徑時渲染(下一節)——頁面不抓任何外部東西。
- 訊息裡的 HTML 是文字,不會被當標籤——JSON 是資料,不是頁面的一部分。
- `author` 顯示在氣泡上方;多個 `author` 都會顯示,但「打字」動畫一律演成同一個人。
- 順序就是播放順序;想剪掉一段就刪那幾則。

### 秀出來的檔案(`show_file`、畫圖工具、`![](路徑)`)

聊天視窗把檔案放到你面前有三條路,影片都照做:

1. **`show_file`**:工具結果尾端一行 `[shown-files]{"shown_files":[{"path":"/plots/a.png","mime":"image/png","size":1234,"caption":"…"}]}`
   (Export 出來就長這樣;手寫也行,`path` / `mime` / `size` 必填、`caption` 選填)。沒有卡片,圖直接出現。
2. **任何工具**結果尾端帶同一行宣告(畫圖工具的輸出會被正規化成它):卡片下面接檔案。
3. **回答裡的 `![](plots/a.png)`**:workspace 路徑就渲染成圖。

`image/*` 內嵌成 260px 縮圖(隨 `--scale` 放大),其他 mime 是檔案卡(檔名 + 大小)。**bytes 不在 JSON 裡**——
要用 `--files DIR` 指到 workspace 資料夾(路徑 `/plots/a.png` ⇒ `DIR/plots/a.png`)。沒給、或檔案不在:
工具宣告的那種變成檔案卡、回答裡 `![](…)` 的那種只剩 alt 文字;指令會在 stderr 逐一說哪個路徑沒畫、為什麼
(不在 DIR 底下或太大 / 不是圖 / 超出總預算);不會炸。`plots/a.png`、`./plots/a.png`、`plots//a.png` 是同一個檔。
`DIR` 之外的路徑(`/../…`、指到外面的 symlink)一律不讀——`../secret.png` 不會被折成 `DIR/secret.png`(聊天視窗對它也是
破圖)。宣告的 mime 不是 `image/*` 的檔(CSV、PDF)照聊天視窗的規則就是檔案卡:不讀、不列在 note 裡;同一個路徑被回答的
`![]()` 和某次宣告都提到,圖片送進頁面時的型別**照聊天視窗的檔案路由用檔名決定**(同一個函式),bytes 一個都不看——同一個
Chromium 決定畫不畫,所以聊天視窗畫得出的影片也畫得出,聊天視窗會破圖的(`.svg` 檔名裝 PNG、0 byte 的 `.png`、`![]()` 指到文字檔)影片
也破圖。頁面自己只在兩種情況不畫:沒拿到 bytes、超預算。宣告的 mime 只決定那次宣告是縮圖還是檔案卡。外部 URL 的 `![](https://…)`
**不會被抓**——頁面不碰網路——
只剩 alt 文字(聊天視窗會抓,這是影片自己的規則)。中文檔名、含空白的路徑(寫成 `<plots/my chart.png>`)、
`![x][ref]` 參照式都認得——timeline 要讀哪些檔和頁面畫哪些圖是同一次 markdown 解析。(這說的是影片;聊天視窗本身目前對中文 /
含空白檔名的 `![]()` 是破圖——前端把已編碼的路徑再編一次——另開票處理。)

一份完整的範例(含 `show_file` 和一張圖):[`docs/examples/chat-video-sample/`](examples/chat-video-sample/chat.json)——

```bash
uv run python -m workspace_app.chat_video docs/examples/chat-video-sample/chat.json \
    --files docs/examples/chat-video-sample -o demo.mp4 --width 1920 --height 1080
```

## 它是怎麼做的

```
.chat.json ─► timeline(純函式:每則訊息 → 一步 + 預估毫秒)
           ─► player.html(自帶 CSS/JS,嵌入 timeline;無 CDN)
           ─► headless Chromium 錄影(viewport = 輸出尺寸)
           ─► ffmpeg 轉 gif / mp4 / webm
```

zoom 是 CSS `transform`(推進 + 平移到輸入框),不是後製;UI 放大是 CSS `zoom`(Chromium 的組合方式;`--html` 用別的瀏覽器預覽可能不同)。全部在
`src/workspace_app/chat_video/`;重依賴(Playwright、ffmpeg)只在 `render.py`,其他部分不裝 extra
也能 import——這是為了之後 worker pod 版:API pod 不需要帶瀏覽器。

## 限制

- 仿真的聊天視窗,像但不是像素級的真 app 畫面。
- 不畫檔案樹、側欄、citation;`ask_user` 的選項畫成一般卡片(和 replay 模式一樣)。
- 跑在沒有 CJK 字型的機器(某些 container)中文會是方塊——裝 `fonts-noto-cjk`。

## 要多少資源(量的,41 秒的範例,1080p)

- **時間**:錄影 = 影片時長(頁面即時播放、即時錄),mp4 轉檔 4–5 秒、gif 兩段共 14 秒。
- **記憶體**(峰值 RSS):Chromium 約 170 MB、Playwright 的錄影 ffmpeg 約 150 MB;轉檔 **mp4 273–320 MB**、**gif 230–640 MB**
  (gif 的第二段有時會在前幾秒填滿一個約 50 張 frame 的佇列然後持平;120 秒的片峰值不比 20 秒的高,所以是有界的、不隨片長長大)。
  錄影和轉檔不同時發生,所以整個流程的峰值就是轉檔那一段。修之前:單段式 gif 4,546 MB、預設參數的 libx264 1,329 MB——
  跑在 worker pod 上的話 memory limit 要照上面的數字給(`kubernetes/base/workers.yaml`)。
- **輸出大小**:720p 41 秒 mp4 1.8 MB、gif 18 MB;1080p mp4 2.5 MB、gif 36 MB。

## 從聊天視窗匯出(#823)

chat header 的 **匯出** 開一個對話框,三種格式一組 radio:**文字 JSON**(`.chat.json`,照舊)、**文字 Markdown**
(`.chat.md`,貼進報告用)、**影片**。三種都可以選範圍,**從最新往回數**:全部 / 最近 N 則 / 自訂從第 k 則到第 m 則
(#1 是最新的一則;選單上每則寫 `#k · 👤/🤖 前幾個字`)。文字直接下載;影片的部分:

- **尺寸**三種輸入法,結果列永遠顯示 `W×H・文字 s×・約 N MB`(估的,不是上限——量過一支比估的多六成):比例(16:9 / 1:1 / 9:16)
  + 解析度滑桿(停點 480p…2160p,只列這個部署允許的)、比例 + 文字大小(小 / 中 / 大 / 特大 = 0.8×…1.6×,畫面跟著放大)、
  直接打寬高(+ 可選文字大小;奇數邊錄影器會取成偶數,表單先取好,所以顯示的就是做出來的)。
- **節奏**:整體速度、打字(毫秒/字)、輸入框推近倍率、最長秒數——其餘旗標用指令列的預設;打超過範圍的值送出時夾到範圍內。
- 送出後 header 的綠點右邊多一顆進度膠囊(排隊中 / 錄影中 / 編碼中,`已用秒數 / 約 預估秒數`),完成後顯示影片路徑、可開啟或下載;
  影片寫進這個 item 的 workspace `/exports/chat-video/<標題>-<時間>.<格式>`(算 workspace 額度),旁邊留著
  `<影片>.chat.json`——那份就是輸入,改一改可以用 API 再送。**取消 = 刪掉 `<影片>.progress.json`**(進度列上的取消鈕,
  或在檔案樹裡直接刪),worker 最慢十來秒停下(心跳 10 秒 + 錄影切片 2 秒)、什麼都不寫;還在排隊的連錄影都不開始。
  失敗的話進度檔留著,裡面一句話寫原因。排隊超過 60 秒沒人接手,膠囊會說「還沒有 worker 接手」;錄到一半心跳停 60 秒,說「worker 沒有回應」。
- 需要這個 item 的「讀取檔案」(影片要拿它秀過的圖)與「新增檔案」兩個權限(有「編輯檔案」的人也算——編輯包含新增);
  沒有的話影片那個選項會鎖住並寫原因。

### 從 API 出固定字句的影片

前端用的就是這條 API,所以要「隨時打後端 API 生成固定字句的影片」不用經過任何對話:

```bash
curl -sS -X POST "$BASE/api/a/rca/items/$ITEM/chat-video" \
  -H 'content-type: application/json' \
  -d '{
    "transcript": {"title": "OOM 事故",
                   "messages": [{"role": "user", "content": "為什麼 API pod 會 OOM？"},
                                {"role": "assistant", "content": "cluster_sweeper 在每顆 pod 讀全表。"}]},
    "options": {"width": 1280, "height": 720, "type_ms": 55},
    "output_path": "videos/oom.mp4"
  }'
# 202 {"output_path": "/videos/oom.mp4", "source_path": "/videos/oom.mp4.chat.json",
#      "progress_path": "/videos/oom.mp4.progress.json", "expected_seconds": 7,
#      "stale_after_seconds": 60, "token": "5b1c…"}
```

`transcript` 是完整的 `.chat.json` 文件(`title` + `messages`,每則至少 `role` 與 `content`;可以只是整段對話的一部分,
但要是一份完整的 JSON);`options` 是 `VideoOptions` 的任意子集;`output_path` 可省(伺服端命名),給了的話**副檔名決定格式**
(`.gif` / `.mp4` / `.webm`,同指令列的 `-o`)。`GET` 同一路徑回這個部署的上限(`max_pixels` / `max_seconds` / `max_output_bytes`,
`config.yaml` 的 `chat_video:`);超過是 422、路徑已有檔是 409,同一支還在做、這個 item 有一支在做、你在別的 item 有一支在做也都是 409
(一句話寫在做的是哪一支)。之後用 `GET …/files/<progress_path>` 看進度(`token` 對得上才是這一支的;不是的話對你來說它已經不在了)、
`DELETE …/chat-video?path=<progress_path>` 取消(排的人自己可以刪;檔案路由的 `DELETE` 要「編輯檔案」)、
`GET …/files/<output_path>` 拿影片(支援 `Range`,所以 `<video>` 拖得動、Safari 也肯播)。

算圖在 `chat-video` worker(pod-split 部署要有 `rca-worker-chat-video`;all-in-one 要 API 自己跑 `rca-app-chat-video` image),
見 [deployment.md §11](deployment.md#11-生產環境注意事項)。
