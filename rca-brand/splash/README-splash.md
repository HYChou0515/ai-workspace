# RCA 3.0 開機畫面 (Splash Screen)

兩個檔案:
- `rca-splash.html` — 完整獨立頁面,直接用瀏覽器打開即可看到效果,也可當獨立載入頁。
- `rca-splash-snippet.html` — 片段版,貼進你現有 app 的 index.html 使用。

## 動畫時間軸 (約 2.6 秒)
1. 0.15–1.0s   三層 A 由外到內依序描繪
2. 1.0–1.4s    橫桿展開
3. 1.25s       頂端紅點彈出 (根因焦點)
4. 2.0s        品牌字 RCA 3.0 淡入上滑
5. 2.4s        slogan 淡入
6. 之後        紅點維持輕微呼吸,等待 app 就緒

自動支援 dark / light mode (跟隨系統),並支援 prefers-reduced-motion。

## 整合方式

### 方法一:片段嵌入 (推薦)
把 `rca-splash-snippet.html` 整段貼到 index.html 的 <body> 最前面。
在 <head> 引入字體 (或改用 app 既有字體):

  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300..600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">

### 載入完成時淡出
splash 不會自己消失,要在 app 準備好時呼叫:

  window.RCASplash.hide();

例如:
  // 你的 app 初始化完成後
  window.RCASplash && window.RCASplash.hide();

  // 或等所有資源載入
  window.addEventListener('load', function(){ window.RCASplash.hide(); });

  // React 範例:在最上層 useEffect 中
  useEffect(() => { window.RCASplash && window.RCASplash.hide(); }, []);

## 行為細節
- MIN_SHOW = 2600ms:即使 app 秒開,也會讓動畫完整播完才淡出 (避免閃一下)。
- 後備逾時 10 秒:若 app 一直沒呼叫 hide(),10 秒後自動淡出,避免卡住。
- 淡出 0.5 秒後會自動從 DOM 移除,不殘留、不擋點擊。

## 可調參數
- 動畫速度:調各 animation-delay / dur。
- MIN_SHOW:想讓 splash 停久一點就調大。
- 顏色:改 CSS 變數 --bg / --stroke / --accent / --sub。
