# slides

Marp 投影片。md 就是簡報本體 —— 改 md 等於改投影片，不需要另外維護一份 pptx。

| 檔案 | 內容 |
|---|---|
| `team-onboarding.md` | 給小組成員的平台導覽。**主檔 6 頁**（使命 / 架構 ①②③ / 未來 / 下一步）＋ **附錄 11 頁**（回合資料流、fan-in queue、兩種 tool、四者的取捨、Sandbox×FileStore、KB 進出、四道安全閘、可靠性、固化階梯、品質關卡） |
| `diagrams/*.mmd` | 圖的**來源**（mermaid） |
| `diagrams/*.svg` | 由 `.mmd` 產生、**deck 實際引用**的圖 |

主檔刻意壓在 6 頁內；細節一律進附錄，附錄不限頁數。

「下一步」那頁的 ② 擴展新業務只保留可對外說明的方法論 —— **專案清單為機密，不寫進檔案**，簡報時口頭補充。

附錄 ③ 為兩種 tool 定了名字：**內建工具**（built-in，跑在平台行程內、拿得到回合 context）與
**外掛工具**（provider，在 sandbox 裡當一支指令跑）。這組詞若確定採用，應該進 `CONTEXT.md`
成為正式用語 —— 目前只活在這份投影片裡。

## 怎麼看

VS Code 裝 **Marp for VS Code**，開 md 直接預覽即可。
圖是預先算好的 SVG，所以 VS Code 預覽、GitHub 上直接瀏覽、CLI 匯出**看到的都一樣**。

## 怎麼匯出

`.marprc.yml` 已設好本目錄需要的選項，**在這個目錄底下**跑就好：

```bash
cd slides

# marp 的 PDF / PNG 匯出需要一顆 Chromium；系統沒裝就指一顆現成的
export CHROME_PATH=$(ls -d ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome | tail -1)

npx --yes @marp-team/marp-cli@4.5.0 team-onboarding.md --pdf          # → team-onboarding.pdf
npx --yes @marp-team/marp-cli@4.5.0 team-onboarding.md --pptx         # → team-onboarding.pptx
npx --yes @marp-team/marp-cli@4.5.0 team-onboarding.md --images png   # 一頁一張 PNG
npx --yes @marp-team/marp-cli@4.5.0 team-onboarding.md -w             # 邊改邊看
```

匯出的 pdf / pptx / png **不進版控** —— 簡報的真相是 md。

## 改圖

改 `diagrams/<name>.mmd`，然後重算 SVG：

```bash
slides/diagrams/build.sh                    # 全部重算
slides/diagrams/build.sh team-onboarding-layers   # 只重算一張
```

配色統一在 `diagrams/mermaid-config.json`，跟投影片的色票對齊；不要在單張圖裡寫死顏色，
節點的 `style` 只用來標示語意（綠 = 領域 / 藍 = 平台 / 橘 = 背景工作 / 黑 = 執行層）。

## 改稿注意

- **一頁一題**。主檔每頁都已經接近滿版，加內容前先想能不能換掉既有的一列
- 加字之後**一定要重新匯出 PNG 看過** —— 溢出頁面不會有任何錯誤訊息，只會被裁掉
- **不要用 raw HTML**（`<div>` / `<br>` / `<span>`）。VS Code 的 Marp 預設關閉 HTML，
  寫了在別人機器上會直接消失。版面槽位一律用標題階層：
  `#` 頁標題 · `##` 副標 · `###` 深色結語橫幅 · `####` 段落小標 · 主檔頁的段落 = 註腳
- 語氣：**已經做到的講已經做到，還沒做的就說還沒做**。這份投影片的用途之一是讓人看見
  還有多少事要做 —— 把所有東西寫成「已完工」會讓整個團隊看起來沒有存在必要
