# Profile 自帶 python 環境:`uv.lock` 決定 sandbox 裡有哪些套件

> **狀態**:設計定案(grill-me 九題),**P1–P13 已實作**(#775 / PR #776)。
>
> 實作與對抗式 review 推翻了設計/宣稱裡的**很多處**,全部就地更正。本文用 ⚠️ 同時標
> 「被推翻的宣稱」和「要當心的代價」兩種,所以那個記號的數量不是前者的計數 —— 不要
> 只讀這段摘要,也不要用數 ⚠️ 來代替讀它們。
>
> 其中**四條各自足以讓功能對使用者完全無效**:venv 建在 shim 不看的地方、shim 用
> symlink 指進 venv(CPython 會解析穿過去)、`uv sync` 因為父目錄權限根本建不出 venv、
> 以及失敗時**錯誤訊息是空的**(讀 `stdout`,而 uv 全寫在 `stderr`)。四條的共通點一樣:
> 兩邊單元測試全綠,而**替身都和被測程式一起錯**。真因是**全 repo 沒有任何測試真的在真
> sandbox 裡跑過一次 `uv sync`**。那個測試現在有了 ——
> `tests/sandbox/test_project_env_e2e.py`,而且它的**大部分會在 CI 跑**(零依賴的專案
> 完全不需要網路),不是只留在 release gate 的 integration 套件裡。
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
   carrier。⚠️ **指法是 wrapper script,不是 symlink —— 這條當初寫錯,而且是致命的。**
   CPython 會解析自己執行檔的真實路徑去找 `pyvenv.cfg`,所以一條**從 venv 外面指進去**
   的 symlink 會直接解析**穿過** venv 落到底層直譯器:`uv sync` 明明裝好了,`python`
   一個套件都 import 不到,而兩邊的單元測試全綠(它們斷言的是 `is_symlink()` /
   `resolve()`,那條壞掉的 link 完全符合)。改成一行 `exec` wrapper,argv[0] 就留在
   venv 裡,`sys.prefix` / `sys.executable` 都指對。**兩種形狀都實測過。**
   ⚠️ 另外被否決的是「把 `<venv>/bin` 直接放進 PATH」——那是啟用 venv 的標準做法,
   普通 exec 可行,但 `bash -lc` 一進 login shell 就被 `/etc/profile` 重設 PATH 砍掉
   (就是 #350 那個坑),而 agent 最常用的就是 `bash -lc`。`.jailbin` 已經有
   `/etc/profile.d` 的護欄,沿用它勝過再多一個要護的東西。⚠️ **`pip` 不指過去** —— 實作時實測 `uv venv` 產出的 `bin/` 只有
   `python`/`python3`/`python3.x`,**沒有 pip**,所以沒有東西可以指。照既有程式碼對
   同類情況的立場(「與其 shim 一個不可能運作的東西,不如讓映像自己的 pip 回答」)
   留給映像。代價是**在有宣告的 workspace 裡 `pip install` 會裝到映像的直譯器**,
   而 carrier 那層沒這問題 —— 也就是說宣告依賴反而讓 `pip` 變差。出路是 `uv add`,
   那句話寫進了 `exec` 工具自己的描述
6. 準備過程透過既有的 **`ToolLog`** 事件串進當下那張工具卡
7. **沒有 `pyproject.toml` 的 profile 走 carrier,`python` 的解析一字不變**

⚠️ **但「完全照舊」這句話我說過頭了,更正**:`UV_PROJECT_ENVIRONMENT` 是**無條件**設在
每一次 exec 上的,包括未宣告的 workspace。所以那裡的使用者打 `uv add` / `uv run` 時,
環境會落在 infra 區而不是他專案旁邊的 `.venv` —— **檔案樹裡看不到,也不再算進額度**。

⚠️ **這句原本寫成「`uv venv` / `uv add`」,那是錯的,實測更正**:`UV_PROJECT_ENVIRONMENT`
只有 `uv sync` / `uv add` / `uv run` 會聽。**`uv venv` 完全不理它**,照樣在 cwd 建 `.venv`;
**`uv pip install` 也不理它**,而且會回

    error: No virtual environment found; run `uv venv` to create an environment

⚠️ **而且「照做會建一個沒人讀的 `.venv`」這句也是錯的,再更正一次,實測**:`uv venv`
**在 cwd 是專案根時其實會聽 `UV_PROJECT_ENVIRONMENT`** —— 只有在沒有 `pyproject.toml`
時才忽略。所以在**有宣告**的 workspace 裡,照著我們自己的錯誤訊息打 `uv venv`,不是留下
一個沒人讀的目錄,而是**就地把剛同步好的環境整個重建、清空**:實測 `import tinydep`
之前可以、之後 `ModuleNotFoundError`。設 `VIRTUAL_ENV` 不能阻止這件事,它移除的是
「非得去打 `uv venv` 不可」的理由。
所以有 project venv 時,exec 環境會同時設 **`VIRTUAL_ENV`** —— 那才是生態系另外半邊讀的
變數。沒有 venv 時它被**主動移除**,不是放著不管:`env` 是 `os.environ` 的複本,而用
`uv run` 起的服務身上帶著指向**服務自己 venv** 的 `VIRTUAL_ENV`。正式環境兩個映像都是
直接 exec 直譯器、沒設這個變數,所以不是線上缺陷 —— 但這個值不該取決於伺服器是怎麼被啟動的。

對他其實是變好的(`.venv/` 本來就在 `sync/ignore.py` 的 `DEFAULT_IGNORES` 裡、不會被
持久化,所以他原本是在為一個我們不保存的目錄付額度),而且無條件設它才能讓宣告與未宣告
兩種 workspace 對 uv 的行為一致。**但它不是「照舊」,不該用那句話蓋過去。**

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

⚠️ **這一條我原本寫錯了,更正。** 我當初寫「失敗不能回 502/503/504,否則前端的
`GATEWAY_CUT` 會無限等待(#714)」—— **那個限制套錯層了**。

`GATEWAY_CUT` 住在 `web/src/hooks/useChatSession.tsx`,管的是**聊天送出那個 POST**:
它的意思是「請求被切斷,但那一輪可能還在跑」,所以維持 streaming 讓串流補上結果。
而**這個失敗不在那個 POST 上** —— `ensure_project_env` 是在 agent 的 `exec` 工具裡、
turn 中途、SSE 串流上發生的,`GATEWAY_CUT` 根本不會被問到。

實際發生的事(查證過):`turns.py` 的 `_run_turn` 接住 turn 裡的任何 `Exception`,
交給 `_terminal_error`,產出 `RunError(message=f"{type(exc).__name__}: {exc}")` 並發布 ——
其 docstring 明寫這是「the raw class+message **for an operator**」。所以使用者看到的是

    ProjectEnvError: `uv sync` failed (exit 2): <uv 的原話>

**看得見、可讀、停住那一輪、帶著營運方要的原始訊息 —— 正是這一節要求的行為,而且既有
機制已經做到,不需要新的處理器。**

(順帶:`SandboxBusy` / `SandboxNotFound` 這兩個既有處理器**刻意**回 503 加
`Retry-After`,理由是「這是轉圈和 bug report 的差別」。所以「一律不准 5xx」本來就不是
這個 repo 的規則。)

### **不攔** `pip install`

使用者要知道自己在幹嘛。改用 **system prompt 正面提示**:
「用 `uv add` 裝套件,它會更新 lock,環境重建之後還在。」

> 否決**攔截 `pip install` 並改叫他用 `uv add`**:那是為單一需求加特例。
>
> 但要知道後果:`uv sync` 會**刪掉不在 lock 裡的東西**(這是 sync 的語意),所以
> `pip install` 裝的東西**下一次冷啟動會消失**。提示寫在 prompt 裡,是因為 agent 會讀
> 它、也會自己打 `pip install`。

### `uv` 從映像來,不做成 bundle

> 否決**做成 carrier bundle**:carrier 那套機制(憑證、體積上限、ABI 錨點)是為
> **第三方作者發布自己的東西**而存在的。`uv` 是平台自己的基礎設施,套進那個框只會讓它
> 背一堆無關的規則 —— 那是為複用而複用。而且 `uv` 必須在 provisioning **能跑之前**就存在。

⚠️ **實作時查出來的重要更正**:正式環境**本來就有 uv**,而且不是靠 `Dockerfile.workspace`。
命令是在 **sandbox-host 的容器裡**跑的(uid + cgroup,沒有 per-sandbox 容器),而
`sandbox-host/Dockerfile` 的 runtime stage 就是 `FROM ghcr.io/astral-sh/uv:...`。
所以 uv 早就在 PATH 上。

而 `sandbox_image`(預設 `workspace-app/sandbox:py312-ds`)**從來沒有任何程式碼拿它去起
容器** —— 它只從 config 一路傳到 `AgentConfig` 就停了(docker 後端已於 #252 廢棄)。
`Dockerfile.workspace` 仍然補上了 uv,讓那個映像自身一致,但**讓功能能動的不是它**。

⚠️ **`kind: local` 是唯一有缺口的**:jail 只 bind-mount `/usr` 和 `/etc`(前一版漏寫了
`/etc`),兩者都不含 `$HOME` —— 所以裝在開發者家目錄底下的 uv 在 jail 裡看不到
(實測 `isolate=True` 時 `exec: uv: not found`,exit 127;`isolate=False` 拿得到
`uv 0.7.5`)。不 jail 則繼承開發者的 PATH,沒問題。

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
lock 把**每一個 distribution 都用 sha256 釘死**,那條路走不通。(先前這裡寫「2353 個
sha256」,那是**本 repo 自己的** lock;這段論證講的是 **profile 的** lock,它是 690 個。
數字取錯檔案,而且它會隨依賴變動 —— 論證不需要那個數字。)真正的路徑是 **hardlink**:uv 為了效能會把
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

**量到了(這台開發機):** 出貨的 `playground/pydeps` profile,**冷快取**(空的
`UV_CACHE_DIR`)`uv sync --frozen --inexact` = **2 秒**,產生 382MB cache、8.4MB venv;
快取熱的時候 `ensure_project_env` = **0.63 秒**。所以以這台機器而論,暖機要解的問題不存在。
⚠️ 但這不是 k8s pod 的網路,也不是冷機器,**這個數字不能當作正式環境的結論**。

⚠️ **而且有一條硬上限,原本沒記到帳上**:`uv sync` 走的是一般 `exec`,而 `exec` **沒有**
per-call timeout 參數 —— 它吃 backend 實例層級的 `exec_timeout`,**預設 60 秒總時長**
(另一條 `log_timeout` 是閒置上限,而 `uv sync` 一直有輸出,所以擋下來的一定是總時長那條)。
也就是說:**在慢網路上,重的 profile 冷啟動會被砍成 exit 124**。
行為本身是可接受的 —— 訊息會是 `` `uv sync` failed (exit 124): timed out after 60s ``,
營運方看得懂 —— 但如果要讓重 profile 在慢環境也能起來,得**調高 `exec_timeout`,或給這條
路徑自己的預算**(程式碼註解說「長工作設 `exec_timeout=0` 靠 `log_timeout`」,那是既有機制)。
**未定案**,而且是部署決定。

⚠️ **workflow 是例外情境,要另外想**:它是排程/事件觸發的,沒有人在螢幕前看進度,
而每個節點撞到冷啟動的時間是純粹的浪費。

---

## 實作要拆的地雷

- **shim 有兩份**:`src/workspace_app/sandbox/local_process.py` 與
  `sandbox-host/src/sandbox_host/local_process.py`(兩個 `isolated_process.py` 是子類別,
  exec 那條直接繼承)。⚠️ 這兩份**已漂了 440 行**且**沒有**逐位元相同的守衛(不像
  `artifact.py`),所以要各改各的。漏一份就是「本機會動、線上不會」。
  ⚠️ **「440 行」這個數字是錯的**:它數的是 `diff` 的輸出行數(含 `NNcNN` 標記與 `---`
  分隔線),不是程式碼差異。當時的真實值是 357 行,**現在是 422 行**(227 增 / 195 刪),
  用 `git diff --no-index --stat` 量。
- ⚠️ **但「兩份」只算了檔案,沒算決策點:決定 `python` 是什麼的地方有三個。**
  每份 `local_process.py` 裡的 `_install_python_shim`(unjailed)之外,`_JAIL_BOOTSTRAP`
  是**第三個**,而且是另一種語言(shell)。第一版只改了前兩個,jail 裡的 `python` 仍是
  兩層 —— 有宣告的 workspace 在 jail 裡照樣拿到 carrier。
- ⚠️ **`<root>/.venv` 的父目錄是 root 所有的,`uv sync` 建不出來。** 實測:

    error: failed to create directory `…/.venv`: Permission denied (os error 13)

  正式環境的 `uv sync` 是 `setpriv` 降權後跑的,而 `<root>/<id>` 是這個服務建的、
  它自己擁有。所以**每一個有宣告的 profile 都會開不起來,而且只在正式環境**——開發用的
  unjailed 路徑不降權,建得好好的。這和 `.home`(#393)是同一個 failure class,答案也
  一樣:`_ensure_venv` 在**用到它的地方**每次 exec 建好並 chown,`IsolatedProcessSandbox`
  覆寫成 chown 給該 uid。**建成空的、且只在不存在時建**:實測 uv 接受一個既有的**空**
  目錄當目標,但目錄裡只要有別的東西就整個拒絕(`not a valid Python environment`),
  而任何「清空再建」都會刪掉這功能剛裝好的套件。
- **正式環境沒有 jail**。`sandbox-host` 的 `IsolatedProcessSandbox` 明寫
  `isolate=False`,隔離是**純 uid + cgroup**。jail 的 `mount --bind` 那套(`/.tools`
  唯讀掛載)**只在 `kind: local` 跑**。正式環境的等價物是「workspace 外的兄弟目錄
  + 檔案權限」,`.home`(#393)就是這樣做的。
- ~~**有東西掃 `{sandbox.root}/*` 嗎?**~~ **查過了,不會。** 孤兒回收靠的是
  `_last_active`,一個**記憶體裡以 handle id 為鍵的字典**,不掃檔案系統;而 ext tool
  cache 那個 `iterdir` 有 `_SHA256.match` 過濾,`.uv-cache` 不會命中。
- ⚠️ **`UV_CACHE_DIR` 一開始只設在兩份 isolated backend 的其中一份**(host 那份),
  另一份每次冷啟都重抓整包。兩份都設了。
- ⚠️ **`ProjectEnvError` 讀錯了流。** uv 把錯誤**和進度**全寫在 `stderr`,`stdout` 一個字
  都沒有,而錯誤訊息格式化的是 `stdout` —— 所以營運方拿到的是
  `` `uv sync` failed (exit 2): `` 後面空白一片,正是「直接失敗但要有足夠 error message」
  這條要求唯一在乎的東西。九條單元測試沒抓到,因為**每個替身都把 uv 的話放在 stdout**,
  跟 bug 一起錯。現在兩條流都帶、`stderr` 在前,而且由真 uv 的 e2e 測試釘住。
- ⚠️ **`uv sync` 預設會把不在 lock 裡的套件移除。** 而準備狀態掛在 `AgentToolContext` 上,
  那是**每輪對話**建一個,所以使用者這輪手動裝的東西**下一輪就無聲消失**(實測
  `Uninstalled 1 package - idna==3.19`)。定案是「`uv add` 是我們建議的路」,不是「我們
  用刪掉別的做法來強制它」。用 uv 自己的 `--inexact`:lock 說要有的照裝,沒說的不動。
- ✅ **正式環境的 uv 版本已經釘住(原本沒有)。** P4 寫的「釘 0.7.5」只在
  `docker/Dockerfile.workspace` —— P7 自己證明那個映像沒人拿來啟動任何東西。實際量到的是
  **三個地方跑三個版本**:開發機 0.7.5、CI 0.12.9、而正式映像的浮動 base
  `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` 當天帶的是 **0.9.30**。這個 feature 讓
  uv 版本第一次決定「使用者的 lock 怎麼被讀」,所以那是不能接受的。
  ⚠️ **釘法不是直覺那個**:`ghcr.io/astral-sh/uv:0.12.9-python3.12-bookworm-slim`
  **不存在**(registry 回 `manifest unknown`)。複合 tag 是最順手的猜法,而且**沒有任何
  流水線會建 `sandbox-host/Dockerfile`**(GitLab 只建 `docker/Dockerfile`),所以寫錯不會
  當場紅,只會在某天有人重建映像時炸。改成覆蓋執行檔
  (`COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /usr/local/bin/`,repo 既有的模式),
  並**實際 build 過**驗證:`BEFORE: uv 0.9.30` → `AFTER: uv 0.12.9`,映像跑起來也是 0.12.9。
  CI 的 `setup-uv` 同步釘同一版 —— 只釘一邊的話,CI 測的就不是正式環境跑的。
- ⚠️ **shim 在 PATH 最前面,而 `uv sync` 從 PATH 挑基礎直譯器 —— 所以 venv 可能建在 shim 上面。**
  之後 shim 又指進那個 venv,`python` 就變成 exec 自己:**無限迴圈、零輸出、不會結束**,
  直到 exec timeout 把它殺掉。使用者看到的是 exit 124 加兩條空的流,裡面沒有任何線索
  ——「直譯器沒起來」和「起來了但在繞圈」長得一模一樣。這在 CI 上連續紅了兩輪才被抓到,
  本機一直重現不出來(本機的 uv 每次都挑得到別的直譯器)。護欄是
  `_usable_project_python`:**逐跳**走 symlink 鏈,任何一跳落在 `.jailbin` 就不採用,
  退回 carrier。⚠️ 護欄本身第一版也錯了兩次 —— 先是只看 `realpath`(整條鏈解到最後,
  中間經過 shim 那跳看不見),再是相對連結一律以起點目錄為基準(於是在 venv 自己的
  `bin/` 裡造出假迴圈,把好的 venv 也擋掉)。兩次都是 e2e 測試抓到的。
- ✅ **uv cache 定案:不共用、也不留 —— 住在 sandbox 自己的 `.home` 裡,關掉就跟著刪。**
  兩條被否決的路各自有實測支撐:
  - **共用一份**:uv 只在**下載時**驗 wheel 的 sha256(lock 裡確實有),之後就**信任自己
    那份已解壓的 `archive-v0/`**。實測把共享 cache 裡一個檔案動手腳,uv **原封不動裝進
    另一個全新 venv 且完全沒察覺**。那是跨 item 的程式碼注入路徑,而 per-item 隔離(#345)
    正是它會打破的東西。`UV_LINK_MODE=copy` 只解決 hardlink 別名(inode 由相同變獨立,
    也量過),解決不了這個。
  - **per-uid 但留著,配一個 sweeper** —— 這條看起來最有道理,而且驅動者現成
    (`sandbox-host/__main__.py` 的 `_reaper_loop` 每 300 秒一跳,同一跳已經在用
    「留到上限 / 最舊優先淘汰 / 活著的絕不動」掃 tool cache)。**但鍵是錯的。**
    正式環境的 host **不是**用 item id 導出 uid —— 它用 `_UidPool`
    (「Freed ids are reused」),`kill` 時 `self._pool.free(...)` 把 uid **回收給下一個
    item**。所以 `.uv-cache/{uid}` 不是「item X 的 cache」,是「現在誰拿著 uid U」:
    A 汙染自己的 cache → A 被 kill → uid 回收 → B 拿到同一個 uid → **從 A 的 cache 安裝**。
    ⚠️ 而「先確認那個 sandbox 還在不在這台 host」擋不住它,**方向還相反**:uid 被回收的
    那一刻正是「沒有活著的 sandbox」,也正是下一個 item 最容易撿走的時刻。
    ⚠️ **「所以正式環境根本沒有穩定的身分可以當鍵」—— 這句我一開始寫太滿,更正。**
    host 的 handle 確實是 per-pod uuid(#366),但 `create` **收得到 `item_id`**
    (`controller.create(spec, body.item_id)` → `self._item_of[handle.id]`),
    `nfs_archive` 就是拿它當鍵的。精確的說法是:**uid 是錯的鍵;`item_id` 拿得到,
    但它是選用的**(`item_id: str | None = None`,只在接了 NFS archive 時才會送)。
    所以 per-**item** 的持久快取是成立的,而且查證後比我一開始評估的更成立:
    `http_client.create` **無條件**把 item id 當 `item_id` 送出去(註解:沒接 archive 的
    host 就忽略它),所以正式環境一定拿得到這個鍵;host 那邊的 `str | None` 是向後相容,
    不是正常路徑。
    ⚠️ **我對它提過的一個疑慮也要收回**:我說「鍵是 `None` 時會依設定旋鈕靜默切換安全
    姿態」——不對,退路是「退回 per-sandbox 快取」,那是**更嚴格**的一邊,退化的是速度
    不是安全。
    真正剩下的是:同一個 item 換 sandbox 時 uid 會變,擁有權要每次重建(和 `.home`
    同一個形狀);它仍然需要回收器(驅動者現成 —— `_reaper_loop`,in-use 用
    `self._item_of` 裡有活 sandbox 的 item,而 item id 不像 uid 會被回收);而「最舊」
    不能用 mtime(只讀命中不會更新它,會剛好刪掉最有價值的),要由使用端蓋章。
    **不是這個 PR 要做的**,而且在量到冷啟動的等待真的會痛之前,先做的應該是放寬這條
    路徑的 `exec_timeout` —— 那把「失敗」變成「等待」,比快取更確定、也更便宜。
    ⚠️ 兩個 backend 在這點不對稱:app 端的 `IsolatedProcessSandbox` 由 item id 導出 uid
    (固定),host 端是 pool。**任何以 uid 為鍵的持久狀態,只在 app 端安全,正式環境不安全。**
  - **per-uid 但留著**:`uid_range` 預設 2,000,000,000,所以「份數有上界」等於沒說 ——
    實際是**每個用過 uv 的 item 一份完整堆疊,連 item 被刪掉都還在**。以出貨的 `pydeps`
    冷同步 382MB 算,**約 50 個 item 吃滿 20Gi**,而且永遠欠一個回收器。

  代價是**每次冷啟動都要重抓**,而這讓前面那條硬上限變得更要緊:`uv sync` 走一般 `exec`,
  吃 backend 的 **60 秒總時長**,慢網路上的重 profile 會被砍成 exit 124。訊息看得懂
  (`` `uv sync` failed (exit 124): timed out after 60s ``),但如果要讓重 profile 在慢環境
  也起得來,**調高這條路徑的 `exec_timeout` 是下一個該做的事**(既有機制:長工作設
  `exec_timeout=0` 靠 `log_timeout`)。四份 backend copy 現在寫法一致,per-uid 的覆寫整個
  移除 —— 沒有持久 cache 之後,那個覆寫沒有東西可以保護,留著只會讀起來像還在保護什麼。
- **uv 的鎖在某些檔案系統上會退化**並印出 `Shared locking is not supported by the
  current platform or filesystem`。共享磁碟區若是 NFS,`uv cache clean` 的安全性沒有
  保證 —— 不影響一般安裝,但清 cache 那個動作要小心。
- **venv 在 infra 區 = 使用者在檔案樹裡看不到它**。這是刻意的(不佔額度、不會被誤刪),
  但 `uv` 預設要在專案旁邊建 `.venv`,得用 `UV_PROJECT_ENVIRONMENT` 指出去。

## 為什麼 venv 不放在 workspace 裡

mirror **刻意不持久化** `.venv`(和 `node_modules/` 一起),但它**會算進使用者的
workspace 額度**。放在 workspace 裡等於**收使用者的錢、卻不保證東西還在,而且他連刪
都刪不掉**。infra 區這個模式本來就存在,不必發明新東西。
