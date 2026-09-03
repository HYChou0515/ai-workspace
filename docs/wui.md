# WUI：讓使用者和 AI 自己做頁面

一個 **WUI** 就是 item workspace 裡的**一個資料夾**，裡面有一份 `*.ai.yaml` 寫著
`view: wui`。點那份 yaml，資料夾就以網頁的形式跑起來。

沒有「發布」這個動作，沒有註冊表，也沒有部署——寫進檔案就會動，跟 `board.ai.yaml` 會變成
看板是同一個機制。

```
銷售儀表板/
  page.ai.yaml     ← view: wui —— 這一行讓資料夾成為 WUI
  index.html       ← 進入點
  app.js  style.css
  data.json        ← 頁面自己存的東西（可有可無）
```

!!! info "名字為什麼是這個怪名字"

    因為叫「網頁」會讓不寫程式的人聯想到「那是工程師的事」——而這正是要打破的聯想。
    WUI 是個新詞，好讓它拿到一個新的心智位置。UI 上一律寫 **WUI**，當專有名詞用。

## 這是為了解決什麼

懂 domain 的人不寫軟體，寫軟體的人不懂 domain。兩邊靠 URD 溝通，交期看工程排程，
**不夠重要的需求會餓死**。

WUI 把這一類工作移出工程排程。工程師的產出從「做應用」變成「發布能力」——寫一支 tool，
發布一次，之後 N 個 domain 的人自己在上面組頁面。如果每個 WUI 還是要工程師參與，
瓶頸只是換了個地方，沒有消失。

判斷成功的標準因此不是「WUI 跑得起來」，而是：**一個原本會餓死的需求，由一個不寫程式的人
做出來，而且他後來還在用。**

## 怎麼開給一個 App

Renderer 是全平台都有的——**手寫一份 `view: wui` 的 yaml 現在就能跑**。被關住的是
「AI 知不知道怎麼做」。

要讓 AI 會主動幫使用者做，在那個 App 的 `app.json` 加一行：

```json
"agent": {
  "skills": ["author-skill", "grill-me", "wui"]
}
```

目前**沒有任何 App 宣告它**，這是刻意的：一旦宣告，每個 item 的 skill 選單就會多一列
`wui`，而那正是測試期之前要避免的「太多人知道」。

!!! warning "宣告了就是預設開啟"

    shared skill 只要被宣告就是 default-on，除非那個 profile 釘了明確的 `skills` 清單
    （`pm` 就是這樣做的）。想要「出現在選單但預設關閉」，兩件事都要做。

## 怎麼讓頁面查得到外部系統

**沒有「WUI 專用」的授權。** 頁面能呼叫的，就是那個 item 的 agent 能呼叫的
**package tool**——同一份 `app.json` 的 `agent.tools`，經 profile 和 item 開關收窄後的
結果。所以維運方要做的事跟平時一樣，沒有第二套：

```json
"agent": {
  "tools": ["read_file", "write_file", "lot-status"],
  "external_tools": { "lot-status": "https://.../tool.manifest.json" }
}
```

第三方的 tool 兩個欄位都要寫（`external_tools` 說去哪拿，`tools` 說這個 App 用不用它）；
第一方的（`sample-tools/`）只要寫 `tools`。細節見
[`tool-authoring.md`](tool-authoring.md) 和 [`extending-the-platform.md`](extending-the-platform.md)。

三件事值得先知道：

- **agent 的內建工具（`read_file`、`exec` …）永遠不通。** 不是安全考量，是型別：
  那些工具是講給模型聽的——會截斷、會在資料後面接一句英文說它截斷了、錯誤是一段散文。
  程式讀到那種東西會靜默地拿到半份 JSON。頁面要平台的能力，補一條 HTTP 路由，不是開一個內建工具。
- **AI 不會自己猜哪些能用。** `read_file` 和 `lot-status` 在它眼裡只是兩個名字，
  所以平台會在 skill 裡把可呼叫的清單**逐個列出來**給它。這也表示 tool 一加上去，
  下一輪對話 AI 就知道了，不用改 skill。
- **頁面自己還要在 yaml 宣告一次**（`tools: [lot-status]`）。那不是安全閘門——伺服器端的
  上限才是——而是揭露：讓人打開一個頁面之前，看得出它會伸手到哪裡。

## 你能拿什麼來寫

**你們平常怎麼寫前端，這裡就怎麼寫。** React、Vue、任何 component library、任何圖表套件
都可以——因為函式庫**住在資料夾裡**，不是從 CDN 拉。

```
銷售儀表板/
  page.ai.yaml         view: wui
  package.json         pnpm-lock.yaml
  src/App.jsx          ← AI 改這裡
  dist/index.html      ← build 產物，這才是頁面
  dist/assets/…js
```

AI 在 sandbox 裡跑 `pnpm install && pnpm build`，產物存進 workspace。實測過：Vite 建出來的
React app 丟進資料夾，元件掛載、state 隨點擊更新、在 `useEffect` 裡呼叫
`workspace.whoami()` 也正常——**bridge 對 React 沒有任何特別之處，它就是個全域物件**。

不想用建置工具也完全可以：把一份 UMD 檔（`chart.umd.js`、`purify.min.js`）放進資料夾，
`<script src="./chart.umd.js">`，渲染時自動內嵌。實測過 DOMPurify 這樣載入並正常運作。

三個設定少一個就靜默失敗，寫進 `vite.config.js`：

| 設定 | 不做會怎樣 |
|---|---|
| `base: "./"` | Vite 預設輸出 `/assets/…`（workspace 根目錄絕對路徑），不會被內嵌，頁面全白 |
| `inlineDynamicImports: true` | lazy chunk 沒被進入點引用，不會內嵌，點下去才壞 |
| `entry: dist/index.html` 寫進 yaml | 不寫的話預設找資料夾根目錄的 `index.html` |

!!! warning "`node_modules` 不會被保存"

    鏡像的預設忽略清單有它（`sync/ignore.py`）。**這對執行期沒有影響**——頁面跑的是
    `dist/` 裡的普通檔案，執行期不需要 `node_modules`。它只在 AI 要 rebuild 時需要，
    而那時 sandbox 是熱的；被回收過就重跑一次 `pnpm install --frozen-lockfile`，
    lock 保證裝出同一份東西。

    **但是：`pnpm` 的 store 一定要和 item 目錄在同一個檔案系統。** pnpm 是用硬連結把
    store 連進 `node_modules` 的，跨檔案系統會靜默退化成整份複製（實測 `links=2`
    vs `links=1`，兩種情況都不吭聲）。

## 頁面的邊界

頁面跑在一個 **null origin** 的 iframe 裡（`sandbox="allow-scripts"`，**沒有**
`allow-same-origin`），所以它拿不到 cookie、碰不到外層 DOM、也呼叫不了 API。
唯一的出口是 `postMessage`，而外層是關卡。平台注入的 runtime 給它 `window.workspace`，
七個動詞，**而且這個集合是封閉的**：

| 動詞 | 範圍 |
|---|---|
| `listFiles` `readFile` `openFile` | 整個 item |
| `writeFile` `deleteFile` | **只有頁面自己的資料夾** |
| `whoami` | 誰在看 |
| `callTool` | 只通這個 App 開放、且頁面在 yaml 宣告過的 tool |

**執行期沒有網路。** 這是說「頁面跑起來之後」——`fetch`、遠端 `<script src>`、web font、
遠端圖片，連「把自己導航到別的網站」都擋掉（那條靠 app 文件的 `frame-src`，見
[`plan-wui.md`](plan-wui.md)）。**建置期不受這個限制**：那是 sandbox 裡的 `pnpm`，
不是瀏覽器。

所以「不能 CDN」的實際意思是**依賴要跟頁面一起被存起來**——對你們碰不到 CDN 的環境來說，
這本來就是你們的做法，而且結果更好：離線可用、版本不會被上游偷換、外網不通也不影響。

要在執行期跟外部系統講話只有一條路：呼叫 tool，由平台去跑——**帳密留在平台，永遠不會進到
瀏覽器**，而且頁面也決定不了要跟使用者要哪一組密碼。以後要加新能力一律從 `callTool` 進來，
不會再多一個動詞，這樣要信任的程式碼面積是固定的。

## 壞掉的時候

這是整件事會不會兌現的分水嶺：一個不寫程式的人打不開 console，只能說「壞了」，
而 AI 看不到瀏覽器裡發生什麼事。

所以**捕捉錯誤的那段程式是平台的，不是 AI 寫的**——它一定在、一定對，即使頁面整個掛掉：

- 未捕捉的錯誤、未處理的 rejection、載不到的檔案、被政策拒絕的請求，全部顯示在頁面上方的
  面板，用白話寫。
- **「回報問題」**進入圈選模式，使用者點下畫面上不對的那一塊。
- **「Tell the agent」**把錯誤、圈到的區塊、它的尺寸與 computed style 一起填進聊天框
  （不會直接送出）。使用者不必學會描述問題。

在頁面的主要區塊掛 `data-wui="料況表"`，回報就會指名是哪一塊。

## 誰在寫這些頁面

AI。它讀的是 `wui` 這個 shared skill（`sample-skills/wui/`），裡面有 how-to、
完整的 API 參考，以及**三份可以直接抄的範例**：

| 範例 | 什麼時候抄 |
|---|---|
| `examples/dashboard/` | 資料已經在 item 裡，有人想換個方式**看** |
| `examples/editor/` | 頁面就是資料被**輸入或修改**的地方 |
| `examples/external/` | 答案在**另一個系統**裡——`callTool` |

小模型照抄比照著規格生成可靠得多，所以範例是這個 skill 最重要的部分。

!!! warning "`external` 那份抄過去不會直接能用"

    它呼叫的 tool 必須是**這個 App 有開放的**。所以那份範例的重點不在成功路徑，
    而在三條失敗路徑各自看得見、而且指向不同的人：**沒在 yaml 宣告**（改 view 檔）、
    **App 沒開放**（找維運方）、**tool 自己說不行**（看 tool 的輸出）。
    一個把三種都顯示成「查詢失敗」的頁面，會有三分之二的機會把人指到錯的地方。

## 已知的限制

- **不會自己重載。** AI 改完頁面，要按面板上的「重新整理」才看得到。
- **不會傳播。** WUI 活在做出它的那個 item 裡；別的 item 要用只能複製資料夾。
- **放在 workspace 根目錄的頁面不能寫入**——它沒有自己的資料夾。讀沒問題。
- `srcset` 不會被解析，寫一個 `src` 就好。
- 網址帶 query string（`logo.png?v=2`）會讓副檔名判斷失效。
- KB 與 wiki 的檔案服務分不出「沒有權限」和「檔案不存在」，兩者都會被當成不存在。

## 延伸閱讀

- [`plan-wui.md`](plan-wui.md)——十五條定案決策連同理由，以及刻意不做的清單。
  要改這塊之前先讀它。
- `sample-skills/wui/reference.md`——七個動詞的完整簽章與錯誤契約。
