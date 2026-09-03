# Profile 自帶 python 環境:`uv.lock` 決定 sandbox 裡有哪些套件

> **狀態**:設計定案(grill-me 九題),**P1–P5 已實作**(#775 / PR #776)。
>
> 實作推翻了設計裡的兩處,已就地更正並標 ⚠️:`pip` 那條,以及 shim 的份數。
>
> 這份文件記的是**為什麼這樣設計、當初考慮過哪些路、為什麼不走**。要改其中任何一條之前,
> 先看它原本被否決的理由。

## 問題

今天 sandbox 裡有哪些 python 套件,是**映像決定的**:`docker/Dockerfile.workspace`
(`python:3.12-slim` + 一組固定的資料分析套件),加上 `python-stack` carrier 把那一套
帶進 `/.tools`。

一個 profile 需要別的東西時,唯一的辦法是**叫 agent 自己 `pip install`** —— 而那條路
會踩到「裝到 A、跑在 B」(#581),而且裝完的東西活不過一次閒置回收。沒有任何地方
「宣告」一個 profile 需要什麼。

**目標**:profile 的起始檔案裡放 `pyproject.toml` + `uv.lock`,sandbox 就自動把環境
準備成 lock 描述的樣子。

---

## 會發生什麼

1. profile 的起始檔案照舊 seed 進 workspace(`apps/seeding.py`),其中包含一份
   **已經列好現有資料分析堆疊**的 `pyproject.toml` + `uv.lock`
2. 每次 sandbox 冷啟動,在**快照還原之後**、`provision_tools` 旁邊,跑一次
   **`uv sync --frozen`**
3. venv 建在 **infra 區**(`root/` 的兄弟目錄,跟 `.ready` / `.home` 同層),
   跟著 sandbox 一起回收
4. uv 的下載 cache 放在 **`{sandbox.root}/.uv-cache/{uid}/`** —— 在 sandbox 目錄
   **外面**,所以撐得過閒置回收
5. `.jailbin` 加**第三層**:有 project venv 時,`python` / `python3` 指向它,優先於
   carrier。⚠️ **`pip` 不指過去** —— 實作時實測 `uv venv` 產出的 `bin/` 只有
   `python`/`python3`/`python3.x`,**沒有 pip**,所以沒有東西可以指。照既有程式碼對
   同類情況的立場(「與其 shim 一個不可能運作的東西,不如讓映像自己的 pip 回答」)
   留給映像。代價是**在有宣告的 workspace 裡 `pip install` 會裝到映像的直譯器**,
   而 carrier 那層沒這問題 —— 也就是說宣告依賴反而讓 `pip` 變差。出路是 `uv add`,
   那句話寫進了 `exec` 工具自己的描述
6. 準備過程透過既有的 **`ToolLog`** 事件串進當下那張工具卡
7. **沒有 `pyproject.toml` 的 profile 完全照舊**,走 carrier

---

## 定案的取捨

### profile 只給「起始值」,不是「宣告」

seed 之後,**使用者 workspace 裡的那份就是真來源**;要加東西就自己 `uv add`。

> 否決的替代方案:把 profile 當權威,以 profile 為鍵快取一份環境給所有 item 共用。
> 那樣「使用者改了 pyproject」要嘛不生效、要嘛得特別處理,而畫面上寫的和實際跑的
> 會不一致。

### `uv sync --frozen`,lock 過期時**明說但不擋**

使用者手改 `pyproject.toml` 沒跑 `uv add` 時,照 **lock** 裝,並在那一輪講明白
「你的 `pyproject` 改過但 lock 沒更新,現在跑的是 lock 的版本」。

> 否決 **預設(自動重解)**:同一份 lock、兩次冷啟動可能裝出不同版本 —— 那 lock 就白放了。
>
> 否決 **`--locked`(過期就失敗)**:失敗會發生在**冷啟動的路徑上**,也就是使用者
> 按下送出等回應的時候,他當下能做的補救是零。
>
> `--frozen` 唯一的缺點是**安靜**,所以配套是那句話一定要說出來。不擋他,但不騙他。

### 有 `pyproject.toml` 就**完全取代** carrier

`python` 指向 project venv,carrier 只服務沒有 pyproject 的舊世界。

> 否決**疊加**(venv 看得到 carrier 的 site-packages):一半的套件版本在 lock 裡、
> 一半在映像裡,`uv.lock` 就不再能回答「我這個環境是什麼」。要 uv 就是要那個保證。
>
> 「取代」的風險是一個作者只想加 `requests`,結果**安靜地失去整套資料分析堆疊**。
> 解法是**起始 `pyproject.toml` 預先列好那一套** —— 起始值本來就是我們可以決定的東西,
> 讓作者從一個能跑的基準上增刪,而不是從零開始不小心弄掉。

⚠️ **代價**:同一套堆疊會存在兩個地方(carrier 映像一份、起始 `pyproject.toml` 一份),
**它們會漂**。要嘛接受 carrier 凍在那裡只服務舊世界,要嘛以後讓 carrier 本身也從那份
pyproject 產生。

### `uv sync` 失敗就**直接失敗**

不降級。訊息要**夠營運方直接動手**(uv 的原始錯誤、哪個套件、哪個 index),同時給
使用者一句看得懂的「這不是你能修的」。

> 否決**降級**(退回 carrier 照跑):這種失敗通常**只有營運方修得動**,降級只會把問題
> 藏起來,讓使用者一輪一輪地撞。而且退回 carrier 之後環境是「看起來合理但其實是錯的」
> —— pandas 還在,profile 指定的東西不在。

⚠️ **這個失敗不能用 502 / 503 / 504 回。** 前端的 `GATEWAY_CUT` 會把 5xx 當成閘道斷線
然後無限等待(#714 踩過),那會讓「直接失敗」在畫面上表現成「永遠轉圈」。
現在的 `ProvisionError` 是直接往上拋、沒有任何顯式處理,這條路徑要一起釐清。

### **不攔** `pip install`

使用者要知道自己在幹嘛。改用 **system prompt 正面提示**:
「用 `uv add` 裝套件,它會更新 lock,環境重建之後還在。」

> 否決**攔截 `pip install` 並改叫他用 `uv add`**:那是為單一需求加特例。
>
> 但要知道後果:`uv sync` 會**刪掉不在 lock 裡的東西**(這是 sync 的語意),所以
> `pip install` 裝的東西**下一次冷啟動會消失**。提示寫在 prompt 裡,是因為 agent 會讀
> 它、也會自己打 `pip install`。

### `uv` 烤進 workspace 映像

> 否決**做成 carrier bundle**:carrier 那套機制(憑證、體積上限、ABI 錨點)是為
> **第三方作者發布自己的東西**而存在的。`uv` 是平台自己的基礎設施,套進那個框只會讓它
> 背一堆無關的規則 —— 那是為複用而複用。而且 `uv` 必須在 provisioning **能跑之前**就存在。

⚠️ uv 的版本因此被映像釘死。這其實是好事(lock 的解讀方式不會在使用者背後改變),
但升級 uv = 重推映像。

### cache 先只做 per-uid,**不做共用層**

`{sandbox.root}/.uv-cache/{uid}/`,owner 是該 uid、mode 0700。

同時滿足:各自可寫、**沒有跨 item 的寫入路徑**、撐得過閒置回收(不在被 rmtree 的樹裡)、
撐得過換 pod(`sandbox.root` 是共享 RWX 磁碟區)。uid 由 `item_id` 雜湊而來且**穩定**,
所以「這個 uid 專屬的目錄」天生就是 per-item 的,不需要協調。

> 否決(**暫緩**)**唯讀共用 + 部署時預填**:per-uid cache 撐得過回收,所以下載是
> **每個 item 一次**,不是每次冷啟動一次 —— 共用層最大的賣點因此縮水。它換來的是
> 「每個 item 的第一次」比較快、磁碟不重複,代價是一個**要跟 lock 保持同步的部署步驟**。
> 在量到那個「第一次」實際上是幾秒之前,不該為沒量過的問題付永久的代價。

⚠️ **如果之後要做共用層,那一份必須是唯讀的。** 理由不是「怕有人塞假 wheel」——
`uv.lock` 有 2353 個 sha256,那條路走不通。真正的路徑是 **hardlink**:uv 為了效能會把
cache 的檔案 **hardlink** 進 venv(官方文件明說 cache 必須和 venv 同一個檔案系統,
否則會退化成慢速複製)。所以

1. item A 下載某套件,cache 檔案 owner 是 A
2. item B 同步,**hardlink 同一個 inode** 進自己的 venv
3. A 事後改寫那個檔案(它是 owner)
4. **B 下次 import 執行到的是 A 寫的東西**

**lock 的雜湊是安裝當下驗的,擋不住安裝之後的竄改。** 共用那份必須 owner=root、
mode 444,誰都改不動,別名才不再是問題。

### **不暖機**

維持懶建立(純檔案操作不開 sandbox)。第一次跑程式的等待,先靠進度顯示讓它可理解,
**先量,再決定要不要暖機**。

> 否決**開 item 就在背景準備**:那是用「所有人都付一點」換「跑程式的人少等一次」,
> 而且會把成本推到一個看不見的地方。而在量到那個等待是幾秒之前,加暖機是替一個
> 沒量過的問題付永久代價。

⚠️ **workflow 是例外情境,要另外想**:它是排程/事件觸發的,沒有人在螢幕前看進度,
而每個節點撞到冷啟動的時間是純粹的浪費。

---

## 實作要拆的地雷

- **shim 有兩份**:`src/workspace_app/sandbox/local_process.py` 與
  `sandbox-host/src/sandbox_host/local_process.py`(兩個 `isolated_process.py` 是子類別,
  exec 那條直接繼承)。⚠️ 這兩份**已漂了 440 行**且**沒有**逐位元相同的守衛(不像
  `artifact.py`),所以要各改各的。漏一份就是「本機會動、線上不會」。
- **正式環境沒有 jail**。`sandbox-host` 的 `IsolatedProcessSandbox` 明寫
  `isolate=False`,隔離是**純 uid + cgroup**。jail 的 `mount --bind` 那套(`/.tools`
  唯讀掛載)**只在 `kind: local` 跑**。正式環境的等價物是「workspace 外的兄弟目錄
  + 檔案權限」,`.home`(#393)就是這樣做的。
- **有東西掃 `{sandbox.root}/*` 嗎?** 多一個 `.uv-cache` 進去,可能被當成孤兒 sandbox
  收掉。實作前要確認。
- **cache 沒有上界**,只會長。需要一個清理策略(可照抄 blob GC 的形狀)。
- **uv 的鎖在某些檔案系統上會退化**並印出 `Shared locking is not supported by the
  current platform or filesystem`。共享磁碟區若是 NFS,`uv cache clean` 的安全性沒有
  保證 —— 不影響一般安裝,但清 cache 那個動作要小心。
- **venv 在 infra 區 = 使用者在檔案樹裡看不到它**。這是刻意的(不佔額度、不會被誤刪),
  但 `uv` 預設要在專案旁邊建 `.venv`,得用 `UV_PROJECT_ENVIRONMENT` 指出去。

## 為什麼 venv 不放在 workspace 裡

mirror **刻意不持久化** `.venv`(和 `node_modules/` 一起),但它**會算進使用者的
workspace 額度**。放在 workspace 裡等於**收使用者的錢、卻不保證東西還在,而且他連刪
都刪不掉**。infra 區這個模式本來就存在,不必發明新東西。
