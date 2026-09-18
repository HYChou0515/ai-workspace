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
| `--speed` | 1.0 | 整體倍速;`2` = 快一倍 |
| `--max-seconds` | 90 | 影片上限。超過的話**所有延遲等比壓縮**,不丟訊息、不截尾。軟上限:瀏覽器每個字的固定開銷壓不掉,幾萬字的對話還是會超過(指令會印出實際會播多久) |
| `--tool-output-chars` | 600 | 工具輸出**和參數**超過就截斷加 `…`(`write_file` 的整個檔案內容不會撐爆卡片) |
| `--max-asset-bytes` | 4000000 | 內嵌的圖超過這個大小就改成檔案卡 |

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

指令會印出「會播多久」(`will play 39.1s`;有壓縮時連原本要多久一起印)。實錄比它長約 3–6%(範例:預設速度
39.1 s 估 → 41.2 s 實錄、`--speed 1.5` 19.7 → 20.2);很短的片子比例更高(4.5 → 5.1 s);被壓縮的片子反而略短
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
要用 `--files DIR` 指到 workspace 資料夾(路徑 `/plots/a.png` ⇒ `DIR/plots/a.png`)。沒給、或檔案不在,
那張圖就變成檔案卡,指令會在 stderr 說哪一個;不會炸。`DIR` 之外的路徑(`/../…`)一律不讀。
外部 URL 的 `![](https://…)` **不會被抓**——頁面不碰網路——只剩 alt 文字。

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
