# WUI：讓使用者和 AI 自己做頁面

一個 **WUI** 就是 item workspace 裡的**一個資料夾**，裡面有一份 `*.ai.yaml` 寫著
`view: wui`。點那份 yaml，資料夾就以網頁的形式跑起來。

沒有註冊表，也沒有部署流程——寫進檔案就會動，跟 `board.ai.yaml` 會變成看板是同一個機制。
（工具列上的 **Deploy** 不是讓頁面「動起來」的開關：頁面本來就會動、本來就有網址。Deploy
做的是重建、確認頁面打得開、把網址端出來，**並且把這一頁列上全平台的 WUI 總覽**——
見下方「發布（Deploy）」。）

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
  src/main.tsx         ← AI 改這裡(TypeScript)
  src/wui.d.ts         ← workspace 橋接的型別,原樣照抄
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

### 要畫圖?用真的圖表庫,不要自己刻

「沒有網路」講的是**執行期**。CDN 上的 `<script src>` 到不了,但函式庫**只要是資料夾裡的一個檔**就行——folder-relative 的引用會和 `app.js`、`style.css` 一樣被內嵌。而 **sandbox 是有網路的**,所以「把檔案弄進資料夾」這件事本身可以自動化:

```json
{ "scripts": {
    "build": "npm pack chart.js@4 --silent && tar xzf chart.js-*.tgz && cp package/dist/chart.umd.js . && rm -rf package chart.js-*.tgz"
} }
```

把它寫成 `scripts.build`,**打開頁面就夠了**:有 build 的頁面開啟時會自動重建,build 去把函式庫抓下來,圖就畫出來了(旁邊的 **Rebuild** 也可以自己按,過程看得到)。

實測(2026-09-04,真瀏覽器、真後端):**Chart.js 4.5.1**(UMD,208 KB)在 `default-src 'none'` 的 null-origin iframe 裡**載入、繪製、hit-testing、tooltip 全部正常,零 CSP 違規**;點柱子觸發頁面自己的下鑽也正常。它沒有用 `eval`/`new Function`,這點要留意——用了那些的函式庫會被 CSP 擋下。

挑小的:檔案是被**內嵌進文件**的,所以它的大小每次開頁都要付一次。頁面大到需要打包工具時,改走 `pnpm add` + build(見 `examples/react/`)。

範例在 `sample-skills/wui/examples/chart/`。

### 誰負責重建

`src/` 改了、`dist/` 沒重建，頁面就會**安靜地**停在舊版本——這是這條路上唯一一種
沒有任何訊息的失敗。所以重建這件事放在看得到的地方：

- 有 build 的頁面，Refresh 旁邊會多一顆 **Rebuild**，按下去**邊跑邊把 build 的輸出
  印在頁面上方**。build 動輒數十秒、而且改到一半失敗是常態，編譯器講的話就是全部的
  價值，只給一顆轉圈圈等於什麼都沒給。
- 旁邊有一個 **Auto-rebuild** 開關（滑鼠移上去有完整說明），**預設是開的**：打開頁面
  就會重建，所以「忘記重建」這件事不會落到讀的人身上。這不是保證——判斷「這頁有沒有
  build」的那次讀取失敗會被靜默吞掉，而 build 失敗時舊的 `dist/` 也還在。它是「選項」
  而不是「規則」，因為代價是真的
  ——每次開都要叫醒 sandbox、等數十秒——所以是**逐頁**記住的（同一個人可以讓快的
  頁面自動建、慢的不要）。
- 只能讀、不能執行的人（沒有 `execute`）按得動，但會拿到一次拒絕；之後不會每次開頁都再被拒絕一次：
  自動重建收到 403 就自己關掉並說明原因（只有 403，避免一次網路抖動就永久關掉）。

Refresh **不會** build，它只是重讀資料夾。所以 AI 改完 `src/` 還是要在**同一輪**
裡自己 rebuild：自動重建保護的是「下次在**工作區**打開這頁的人」，不是「現在正看著
這頁的人」——也不是拿連結的人，他們看到的永遠是資料夾**現在**建好的成品（見下一節）。

!!! warning "`node_modules` 不會被保存"

    鏡像的預設忽略清單有它（`sync/ignore.py`）。**這對執行期沒有影響**——頁面跑的是
    `dist/` 裡的普通檔案，執行期不需要 `node_modules`。但 sandbox 被回收過之後，
    相依就不見了。所以 Rebuild 這條路是**先 install 再 build**（有 lock 就
    `--frozen-lockfile`，沒有就寫一份出來）：熱的時候多花約一秒，冷的時候會自己長回來，
    不需要有人知道「要先跑 pnpm install」。

    **但是：`pnpm` 的 store 一定要和 item 目錄在同一個檔案系統。** pnpm 是用硬連結把
    store 連進 `node_modules` 的，跨檔案系統會靜默退化成整份複製（實測 `links=2`
    vs `links=1`，兩種情況都不吭聲）。

### 發布（Deploy）：給別人一個網址

每一頁都有自己的網址——`/w/{app}/{item}/{view 檔的路徑}`（部署在子路徑下時，前面
再接那個子路徑；Deploy 端出來的網址已經接好）——打開就是這一頁本身，
**沒有工作區的外殼**：沒有導覽列、沒有檔案樹、沒有麵包屑。跟著連結來的人無處可去，
那些東西對他只是噪音。

工具列最右邊有一顆 **Deploy**：

- 按下去做四件事：**重讀** `package.json`（不信開頁時的快取——頁面可能是開著之後才被
  加上 build 的）；有 build 就**先 rebuild**；**確認頁面真的打得開**（走的是跟讀者
  開頁時同一條讀取路徑；讀者開頁時仍是他自己那一刻的讀取）；最後**把這一頁列上
  WUI 總覽**（見下一節）——四件都成才把網址端出來，所以網址指到的一定是剛建好、
  **當下**打得開、而且別人找得到的成品，不會是舊的 `dist/`，也不會是一個 `entry`
  指錯地方的空頁。任一步失敗就不會出現「Deployed」，而是一句說明是哪一步：build 壞了是
  「Deploy failed — see the build output」壓在 build 輸出上面；打不開是「Deploy failed —
  the page does not open: …」帶頁面自己的理由；`package.json` 讀不到是「could not check
  whether this page has a build: …」；總覽不收是「Deploy failed — the page could not be
  listed in WUI: …」帶伺服器的理由（最常見的是沒有這個 item 的 `edit_content`——能改
  item 內容的人才能把頁面上架）。什麼都不端出來。
- 跑的期間**整個 pane 是 Deploy 的**：Refresh、Rebuild、Auto-rebuild 都按不下去（同一個
  資料夾裡不能有兩個 build 在寫 `dist/`），旁邊多一顆 **Cancel**——一個永遠不結束的
  build（卡住的 `pnpm run build`、被 gateway 吊著的串流）不會把 pane 鎖到關檔為止。
  Cancel 會中止 build、在 build 輸出裡寫一行「Cancelled.」，而且那一次什麼都不會端出來。
- 同一個資料夾裡有**兩份 view 檔**時，判定是**每一頁各自的**：在 A 按 Deploy、切到 B、
  A 跑完——B 不會被重載（設計決定 9），A 的結果等你切回 A 才套用。等的期間如果 pane
  又讀過一次資料夾（在 B 按了 Refresh、Rebuild 或 Deploy），回到 A 看到的是
  「Deploy stopped — the pane was refreshed, rebuilt or deployed again before it was
  back on this page. Deploy again.」——不會假裝那個結果還算數，也不會無聲消失。
- 面板上是網址、**Copy**、**Open**，和一句話：**能打開這個 item 的人才能用這個連結。**
  網址是**捷徑，不是授權**——它不給任何人原本沒有的權限，API 在這裡拒絕的跟在工作區裡
  拒絕的一模一樣。要給 item 外的人看，先把他加進 item。

拿到連結的人看到的是**讀者版**：

- **不會 rebuild。** 開頁不叫醒 sandbox、不跑 build；讀者**開頁那一刻**拿到的是資料夾
  當時的成品——最近一次 build 的結果，不論那次是 Deploy 還是 Rebuild、還是別人打開時的
  Auto-rebuild 建的；沒有 build 的頁面則是 `index.html` 當時的樣子。讀者不建，發布者建——
  這是 Deploy 為什麼一定要先 build 的原因，但它不是唯一會改變讀者看到什麼的動作。
- **開著的頁面不會自己換掉。** 跟工作區裡一樣（設計決定 9），頁面從不自己重載——
  一個正在填表的人不會被 agent 的一次存檔打斷。所以工作區裡的 build 影響的是**下一次**
  打開或重新整理這個連結的人；正看著的人要看到新版得自己重新整理。
- 沒有工具列、沒有 build 輸出、沒有 reports。
- 頁上的按鈕（tool、workflow）**照常能按**——那是這頁存在的理由。sandbox 只在真的按了
  才會被叫醒。
- 沒建過的頁面會看到「This page has not been published yet — or it is still being
  restored.」和一顆 **Try again**（只重讀、不 build）。保守的措辭是刻意的：在這個平台上
  sandbox 還原到一半時讀檔會答「不存在」，一個發布過的頁面可能有幾秒鐘看起來像沒發布。

所以：改了 `src/` 之後要讓拿連結的人看到新版，**再按一次 Deploy**（Rebuild 也行——
兩者跑的是同一個 build；Deploy 多做的是確認頁面打得開、再把網址端出來）。反過來說，
一個別人拿著連結在用的頁面，你在工作區裡的每一次 build 都會被他**下一次**打開時看到——
要放心動手而不影響他，先複製一份資料夾（見下方「沒有快照」）。

!!! note "沒有快照"

    Deploy 不會凍結任何東西——連結永遠指向資料夾**現在**的成品。要一個不會變的版本，
    把資料夾複製一份（`pages/report/` → `pages/report-v2/`）再改那份，舊連結不動。
    設計理由與被否決的路在 `docs/plan-wui-deploy.md`。

### WUI 總覽：別人怎麼找到這一頁

導覽列有一個入口叫 **WUI**（`/wui`），列出**所有 Deploy 過、而且你開得了的**頁面，
按 app 分組、最新 Deploy 的在前。預設是**卡片**：每一頁一張，上緣一條所屬 App 顏色的色帶、
左邊是頁面的圖示（一個圓圈，見下）、粗體的頁面名字、下面一行它所在的 item（點了進 item 的
工作區）和誰在什麼時候 Deploy 的、右上角一顆星（我的最愛，見下）、右下角**下架**。標題最多
兩行、下面那行只有一行，放不下的用 … 收掉（滑上去看全文）。**整張卡片點下去就在新分頁開
讀者版**（星星、下架、item 名各自按各自的，不會連帶開頁）。標題右邊有「卡片 /
表格」的切換，**表格**是同樣的內容一列一行；選了哪個會記在這個瀏覽器裡。這是頁面「被找到」
的唯一地方：一頁做好了但沒按 Deploy，只有拿到連結的人知道它存在。

- **只列 Deploy 過的。** 不是每個 `view: wui` 檔都會出現——平台不掃描 workspace 找頁面，
  Deploy 那一下才是「這頁是給別人用的」的宣告。舊頁面要出現在總覽，到頁面上按一次 Deploy。
- **名字取自 view 檔的 `title:`**（沒寫就用資料夾名；放在 workspace 根目錄、沒有資料夾的頁面
  用檔名），Deploy 當下由伺服器讀進去；改了 `title:` 之後再 Deploy 一次，總覽就跟著換。
- **圖示取自 view 檔的 `icon:`**，跟 App 的圖示一樣有三種寫法：頁面資料夾裡的一個圖檔名
  （`icon: logo.png`，跟 `entry:` 一樣相對於頁面資料夾）、一個 emoji（`icon: "📦"`）、或平台
  內建的圖示名（`icon: kanban`）。一樣是 Deploy 當下讀進去、改了要再 Deploy。**沒寫、或寫了但
  對不上**（圖檔不在、圖示名沒有這個）的頁面，圓圈裡是標題的第一個字（中文取首字、英文取
  前兩個字的字首），底色是所屬 App 的顏色——所以每一列的左緣永遠是一個同樣大小的圓，
  在總覽上看到圓圈裡是字，就知道 `icon:` 沒生效。Deploy 不會因為 `icon:` 寫錯而失敗。
- **我的最愛。** 每一列右邊有一顆星，按了這一頁就多列在總覽最上面的「我的最愛」群組（在
  它自己的 App 群組裡照樣列著——App 群組是完整清單，我的最愛是捷徑）；再按一次拿掉，一個都
  沒有時這個群組就不出現。星是**你的、存在這個瀏覽器裡**（依登入者分開，換一台電腦看不到）
  ，不上伺服器、別人看不到；能開頁面就能加星，不需要能 Deploy。標了星的頁面被下架或
  你失去權限時，那一列自然消失，星還記著——再 Deploy 回來就還在我的最愛裡。
- **你看到的就是你開得了的。** 總覽按 item 的讀取權過濾，跟連結本身一樣不給任何人多的
  權限；別人的私有 item 裡的頁面在你這裡不存在。item 被刪、或你被移出 item，那幾列就
  自己消失。
- **下架** 只有能 Deploy 的人（有 `edit_content`）看得到，按了先確認一次：它是 Deploy 那步
  「上架」的反向——頁面和資料夾都留著，只是不再列在總覽；要列回來再按一次 Deploy。
- **資料夾被刪掉，那一列不會自己消失**——這是刻意的。這個平台上「檔案不存在」也是
  sandbox 還原到一半時的回答，靠它自動下架會讓頁面在每次閒置回收後閃一下不見、還得再
  Deploy 一次。死掉的列點進去是讀者版那句「This page has not been published yet — or it
  is still being restored」，由有權限的人按下架收掉。

決策與被否決的替代方案（例如在寫入時索引每個 `view: wui` 檔）在 `docs/plan-wui-overview.md`。

## 頁面的邊界

頁面跑在一個 **null origin** 的 iframe 裡（`sandbox="allow-scripts"`，**沒有**
`allow-same-origin`），所以它拿不到 cookie、碰不到外層 DOM、也呼叫不了 API。
唯一的出口是 `postMessage`，而外層是關卡。平台注入的 runtime 給它 `window.workspace`，
動詞的完整清單如下，而這個集合只在一種情況下會變：新增一個要先寫下它為什麼不能從
`callTool` 進來的論證。（刻意不寫數字——上一版寫「七個」而 bridge 有八個，而一個手寫的數字沒有任何守衛看得到它過期。）

| 動詞 | 範圍 |
|---|---|
| `listFiles` `readFile` `openFile` | 整個 item |
| `writeFile` `deleteFile` | **只有頁面自己的資料夾** |
| `whoami` | 誰在看 |
| `callTool` | 只通這個 App 開放、且頁面在 yaml 宣告過的 tool |
| `startRun` | 起一個 workflow 並把進度串回來 |

**執行期沒有網路。** 這是說「頁面跑起來之後」——`fetch`、遠端 `<script src>`、web font、
遠端圖片，連「把自己導航到別的網站」都擋掉（那條靠 app 文件的 `frame-src`，見
[`plan-wui.md`](plan-wui.md)）。**建置期不受這個限制**：那是 sandbox 裡的 `pnpm`，
不是瀏覽器。

所以「不能 CDN」的實際意思是**依賴要跟頁面一起被存起來**——對你們碰不到 CDN 的環境來說，
這本來就是你們的做法，而且結果更好：離線可用、版本不會被上游偷換、外網不通也不影響。

要在執行期跟外部系統講話只有一條路：呼叫 tool，由平台去跑——**帳密留在平台，永遠不會進到
瀏覽器**，而且頁面也決定不了要跟使用者要哪一組密碼。新能力的預設出口是 `callTool`，因為要信任的程式碼面積該是固定的。

⚠️ 這一段原本斷言這個集合永遠不會再長，而 `startRun` 已經是第八個——它值得一個動詞的理由
寫在 `protocol.ts` 裡:一個要跑好幾分鐘、會串事件回來的 run，塞進「呼叫一個 tool 然後拿到
回傳值」的形狀就得讓頁面自己輪詢。這裡的教訓不是那個決定錯，是**一句寫死的「不會再有」
會在它被推翻的那天靜默變成假的**：改它的那個 commit 修了測試裡手抄的動詞清單，沒有修
旁邊這句散文。所以現在這張表由 `tests/apps/test_wui_skill.py` 從 `bridge.ts` 導出來對，
數字不再是手寫的。

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
完整的 API 參考，以及**可以直接抄的範例**：

| 範例 | 什麼時候抄 |
|---|---|
| `examples/complete/` | **先讀這一份**——一頁把整個介面用過一遍:讀、畫圖、存、呼叫 tool、開一個要跑幾分鐘的 run、以及每一種失敗都是一句話 |
| `examples/dashboard/` | 資料已經在 item 裡，有人想換個方式**看** |
| `examples/editor/` | 頁面就是資料被**輸入或修改**的地方 |
| `examples/external/` | 答案在**另一個系統**裡——`callTool` |
| `examples/chart/` | 有人想**看見數字的形狀**——真的圖表庫,build 負責把它抓進資料夾 |
| `examples/react/` | **預設就用這份**——React + TypeScript,真的 build(`pnpm build` → `dist/`) |

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
- `sample-skills/wui/reference.md`——每個動詞的完整簽章與錯誤契約。
