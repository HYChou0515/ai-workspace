# 把一段對話做成影片

把 Export 按鈕下載的 `.chat.json` 變成一段動畫:user 的訊息被逐字打進輸入框(鏡頭推進)、送出、
AI 的思考與回答逐字串流、工具呼叫變成卡片。**不打 LLM、不起 API server**——一條指令,本機跑完。

設計與之後的 job / worker 版形狀見 [plan-chat-video.md](plan-chat-video.md)。

## 安裝(一次)

```bash
uv sync --extra chat-video          # Playwright(Python 套件)
uv run playwright install chromium  # headless Chromium,約 150 MB,放在 ~/.cache/ms-playwright
which ffmpeg                        # 沒有就 apt install ffmpeg
```

沒裝的話指令會用一句話告訴你少了什麼,不會噴 traceback。

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
| `--width / --height` | 1280 / 720 | 輸出像素。**錄影就是 viewport**,不縮放 |
| `--chat-width` | 760 | 聊天欄寬度(CSS px,放大前) |
| `--scale` | 0 = 自動 | 整個 UI 的放大倍率;自動 = 畫面相對 1280×720、不小於 1(1080p 是 1.5、4K 是 3) |
| `--zoom` | 1.8 | user 打字時鏡頭推進的倍率;`1` = 不推。會自動封頂讓整個輸入框進得了畫面 |
| `--zoom-ms` | 900 | 推進 / 拉遠的時長 |
| `--type-speed` | 55 | 打字:每個字幾毫秒(標點停 3 倍) |
| `--stream-speed` | 22 | 串流:每個字幾毫秒 |
| `--tool-pause` | 1200 | 工具卡片轉圈多久 |
| `--speed` | 1.0 | 整體倍速;`2` = 快一倍 |
| `--max-seconds` | 90 | 影片上限。超過的話**所有延遲等比壓縮**,不丟訊息、不截尾 |
| `--tool-output-chars` | 600 | 工具輸出超過就截斷加 `…` |

常用組合:

```bash
# 投影片:1080p 的 MP4,順便一份 GIF
uv run python -m workspace_app.chat_video my.chat.json -o demo.mp4 --fmt gif --width 1920 --height 1080

# 正方形、快一點、不推鏡頭
uv run python -m workspace_app.chat_video my.chat.json -o sq.gif --width 1080 --height 1080 --speed 1.5 --zoom 1

# 先看再錄
uv run python -m workspace_app.chat_video my.chat.json --html preview.html && xdg-open preview.html
```

指令會印出預估秒數;實錄通常在 3% 內(估算含瀏覽器每個字的固定開銷)。

## JSON 怎麼改

檔案就是 Export 的格式:`{"title": "...", "messages": [...]}`,每則訊息是 `Message` 的欄位。
手改時只有這幾個欄位會影響畫面:

| `role` | 用到的欄位 | 畫面 |
|---|---|---|
| `user` | `content`、`author` | 鏡頭推進輸入框,逐字打,送出 |
| `assistant` | `content`(markdown)、`reasoning`、`author` | 有 `reasoning` 先串流灰色「思考」區塊,再串流正文;`content` 空就只有思考 |
| `tool` | `tool_name`、`tool_args`、`content`(= 輸出) | 工具卡片:名稱 + 參數 → 轉圈 → 輸出 |
| `error` | `content`、`error_kind` | 紅色氣泡 |
| 其他(`system`、`mention`、…) | `content` | 一行灰色置中提示 |

- markdown 支援標題 / 粗體 / 清單 / 行內碼 / 程式碼區塊 / 表格;**連結和圖片會變成純文字**(頁面不抓任何外部東西)。
- 訊息裡的 HTML 是文字,不會被當標籤——JSON 是資料,不是頁面的一部分。
- `author` 顯示在氣泡上方;多個 `author` 都會顯示,但「打字」動畫一律演成同一個人。
- 順序就是播放順序;想剪掉一段就刪那幾則。

一份最小的範例:[`docs/examples/chat-video-sample.chat.json`](examples/chat-video-sample.chat.json)。

## 它是怎麼做的

```
.chat.json ─► timeline(純函式:每則訊息 → 一步 + 預估毫秒)
           ─► player.html(自帶 CSS/JS,嵌入 timeline;無 CDN)
           ─► headless Chromium 錄影(viewport = 輸出尺寸)
           ─► ffmpeg 轉 gif / mp4 / webm
```

zoom 是 CSS `transform`(推進 + 平移到輸入框),不是後製;UI 放大是 CSS `zoom`。全部在
`src/workspace_app/chat_video/`;重依賴(Playwright、ffmpeg)只在 `render.py`,其他部分不裝 extra
也能 import——這是為了之後 worker pod 版:API pod 不需要帶瀏覽器。

## 限制

- 仿真的聊天視窗,像但不是像素級的真 app 畫面。
- 不畫檔案樹、側欄、附件圖片、citation。
- 跑在沒有 CJK 字型的機器(某些 container)中文會是方塊——裝 `fonts-noto-cjk`。
