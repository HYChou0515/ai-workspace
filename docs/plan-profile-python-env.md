# Profile 自帶 python 環境:`uv.lock` 決定 sandbox 裡有哪些套件

> **狀態**:設計定案(grill-me 九題),**實作中**(#775 / PR #776)。
>
> ⚠️ 這行原本寫「P1–P**N** 已實作」,而那個 N 已經過期兩次(P30 時寫著 P27,P32 時寫著 P31)——每加一個 phase 就要有人記得回來改一個數字,這種欄位保證會錯。要知道做到哪,看 `git log --oneline` 的 phase 前綴,那是唯一不會落後的來源。
>
> 實作與對抗式 review 推翻了設計/宣稱裡的**很多處**,全部就地更正。本文用 ⚠️ 同時標
> 「被推翻的宣稱」和「要當心的代價」兩種,所以那個記號的數量不是前者的計數 —— 不要
> 只讀這段摘要,也不要用數 ⚠️ 來代替讀它們。
>
> 其中**五條各自足以讓功能對使用者完全無效**:venv 建在 shim 不看的地方、shim 用
> symlink 指進 venv(CPython 會解析穿過去)、`uv sync` 因為父目錄權限根本建不出 venv、
> 失敗時**錯誤訊息是空的**(讀 `stdout`,而 uv 全寫在 `stderr`),以及 shim 排在 PATH
> 最前面讓 `uv sync` **把 venv 建在 shim 上**(於是 `python` exec 自己,無限迴圈、零輸出)。
> 最後那條只有 CI 重現得出來,是它自己一類;**前四條的共通點是同一個**:
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
4. uv 的下載 cache 放在 **`{sandbox.root}/.uv-cache/{item_id}/`** —— 在 sandbox 目錄
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

### cache 每個 **item** 一份,**不做共用層**

`{sandbox.root}/.uv-cache/{item_id}/`,owner 是當下那個 sandbox 的 uid、mode 0700,
每次 exec 重建擁有權(目錄屬於 item,而 uid 是每個 sandbox 配一次的)。

⚠️ **原本這一節寫的是 per-uid,而且理由是「uid 由 `item_id` 雜湊而來且穩定,所以天生就是
per-item 的」—— 那句話對 app 端成立、對正式環境是錯的,已在下面的實作地雷區更正。**
正式環境的 host 用 `_UidPool`(「Freed ids are reused」),`kill` 就把 uid 還回去,所以
uid 不是身分、是租約。鍵改成 item id 之後這一節的結論不變:**不共用**。

不做共用層的理由也更新了,而且是實測不是顧慮:uv 只在**下載時**驗 wheel 的 sha256,
之後就信任自己那份已解壓的 `archive-v0/` —— 在共享 cache 裡動過手腳的檔案會被
**原封不動裝進另一個全新 venv,uv 一聲不吭**。`UV_LINK_MODE=copy` 只解決 hardlink
別名(inode 由相同變獨立,也量過),解決不了汙染。

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
✅ **已修**:`exec` 現在收一個**每次呼叫自己的**總時長預算,而 sync 帶
`_SYNC_BUDGET = 900 秒`。做法照 `env` 那條先例:選用參數、沒有就不放進 HTTP body、
舊 host 忽略。**IDLE 上限完全沒動**,所以真的卡住的下載照樣 60 秒被殺 —— 這個大預算
只有「一路都在前進」的指令才碰得到,不是把守衛拆掉。
兩半各自釘了測試(app 端 `tests/sandbox/test_http.py`、host 端
`sandbox-host/tests/test_wire.py`):只要有一半吞掉這個 key,整個 hosted 部署就會**安靜地
沒生效** —— 和當年 env 那次同一個形狀,所以兩半都要有人守。

⚠️ **workflow 是例外情境,要另外想**:它是排程/事件觸發的,沒有人在螢幕前看進度,
而每個節點撞到冷啟動的時間是純粹的浪費。

---

## 實作要拆的地雷

- ⚠️ **也不要在 commit 訊息裡記 pass 數,除非同時寫清楚跑了哪個子集。** 這條分支上有一次
  差一(宣稱 1053、實測 1054),而且子集中途悄悄多了一個檔案。那些數字還跟環境綁死
  (docker / userns / node / uv 四種 skipif),所以在別台機器上根本不是可重跑的斷言。
- ⚠️ **宣稱「自動產生」之前,先把產生器放進 repo。** 有兩個 commit 說 shim 測試的雙胞胎是
  產生的,而那個腳本只存在於跑它的那台機器上 —— 讀者無法查證也無法重跑。它現在在
  `scripts/gen_host_shim_twin.py`。
- **shim 有兩份**:`src/workspace_app/sandbox/local_process.py` 與
  `sandbox-host/src/sandbox_host/local_process.py`(兩個 `isolated_process.py` 是子類別,
  exec 那條直接繼承)。⚠️ 這兩份**已漂了 440 行**且**沒有**逐位元相同的守衛(不像
  `artifact.py`),所以要各改各的。漏一份就是「本機會動、線上不會」。
  ⚠️ **不要在這裡記行數。** 原本寫「440 行」,那數的是 `diff` 的輸出行數(含 `NNcNN`
  標記與分隔線),不是程式碼差異;更正成 422 之後,它又過期了一次。**要看就當場量**:
  `git diff --no-index --stat <兩份檔案>`。一個會過期的數字,比沒有數字更會誤導人。
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
- ✅ **uv cache 定案(第三版,也是最後一版):不共用、但**留**—— 每個 **item** 一份,
  放在 sandbox 目錄旁邊,活得比 sandbox 久,由既有的 idle tick 依上限淘汰。**
  三個版本各自被什麼推翻,記在這裡免得有人再繞一圈:
  1. **per-uid 且留著** —— 錯在**鍵**。正式環境的 host 用 `_UidPool`(「Freed ids are
     reused」),`kill` 就把 uid 還回去,所以 `.uv-cache/{uid}` 是「現在誰拿著這個 uid」;
     A 汙染自己的 cache → 被 kill → B 拿到同一個 uid → **從 A 的 cache 安裝**。
     而且「先確認那個 sandbox 還在不在這台 host」擋不住,**極性還相反**:uid 被釋放的
     那一刻正是 sweeper 覺得可以刪、也是下一個 item 最容易撿走的時刻。
  2. **跟著 sandbox 一起死** —— 安全但貴:每次冷啟動重抓整包。
  3. **per-item 且留著**(現行)—— item id **不會被回收**,所以只有填它的那個租戶碰得到;
     `.jailbin` 那條路只有 jail 沒有(chroot root 就是 sandbox 目錄),jail 維持 in-sandbox。
     ⚠️ 共用一份**仍然不行**,而且是實測不是顧慮:uv 只在**下載時**驗 sha256,之後就信任
     自己那份已解壓的 `archive-v0/` —— 動過手腳的檔案被**原封不動裝進另一個全新 venv**,
     uv 一聲不吭。`UV_LINK_MODE=copy` 只解決 hardlink 別名,解決不了汙染。
  **回收器的驅動者不用新造**:host 是 `_reaper_loop`(每 300 秒,同一跳已經在掃 tool
  cache),app 是 `idle_killer`。政策照抄 tool cache(#674):**沒設上限就不淘汰**、
  **in_use 絕對優先**(活著的 sandbox 可能還在寫的那份永遠不動)、**最舊優先**——而
  `_exec_argv` 每次 exec 會蓋一次章,否則只讀命中不會更新 mtime,會剛好把最有價值的刪掉。
  旋鈕:`sandbox.uv_cache_max_bytes` / `SANDBOX_HOST_UV_CACHE_MAX_BYTES`,已記進
  `docs/migrations.md` §5.5(含「不設會怎樣」)。
- **uv 的鎖在某些檔案系統上會退化**並印出 `Shared locking is not supported by the
  current platform or filesystem`。共享磁碟區若是 NFS,`uv cache clean` 的安全性沒有
  保證 —— 不影響一般安裝,但清 cache 那個動作要小心。
- **venv 在 infra 區 = 使用者在檔案樹裡看不到它**。這是刻意的(不佔額度、不會被誤刪),
  但 `uv` 預設要在專案旁邊建 `.venv`,得用 `UV_PROJECT_ENVIRONMENT` 指出去。

- ⚠️ **搬走一段工作,也會搬走它順手提供的保證。** P27 把環境準備從 turn 的 pre-warm
  移到 agent 第一次 exec —— 理由是對的(pre-warm 沒有 sink,而且成功會被記住,導致
  `uv lock --check` 整輪都不跑)。但 pre-warm 同時是**唯一的序列化點**:它在
  `_events` 之前 await 完,所以任何 tool 跑起來時旗標已經是 True。移走之後,同一則
  assistant 訊息裡的兩個 tool call(SDK 每個開一個 task、不設上限,而正式環境的後端
  確實會平行發)就會在同一個專案目錄開兩個 `uv sync`。實測 1 → 2。同一個窗口對
  `create` + `provision_tools` 本來就開著(只是被 pre-warm 蓋住),所以答案是
  `AgentToolContext` 上一把 `asyncio.Lock` 蓋住整個 `ensure_sandbox`。
  **看新機制對不對,用 regression lens,不要用 defect lens**:單獨讀那行修改它是對的
  —— 它就是為了對才被寫出來的 —— 壞掉的是舊機制**順便**做的事。
- ⚠️ **同一個順手保證的第二受害者:workflow 的 `run:` 節點。** 它走
  `registry.ensure_handle`,從來沒要過環境;以前是靠前面任何一個 agent 節點的 pre-warm
  順手準備好。現在沒有了,`python` 靜默退回 carrier,跑出一個沒有 profile 套件的答案。
  順帶把「`run:` 節點前面沒有 agent 節點」這個**一直都壞**的情況一起補掉。
- ⚠️ **per-item 的資源,不能在 per-handle 的事件上釋放。** host 的 `kill` 是按 handle 的,
  而 uv cache 是按 item 的,且 host 每次 create 都發新 uuid —— 所以一個 item 可以有兩個
  活著的 sandbox,那正是 #366 重建競賽每次都會產生的狀態(兩個 app pod 喚醒同一個冷
  item,輸的那個 kill 掉自己的孤兒)。在那個 kill 上把 cache 交還服務,等於把**贏家**
  正在 `uv sync` 的 cache 權限收走(EACCES → turn 死)。要問「這是不是這個 item 最後
  一個活著的 sandbox」。
- ⚠️ **同一個 override 在 app 側連理由都不成立。** 它的 docstring 說「這個 backend 把
  uid 還給 pool」,但同一個檔案往上十二個方法的 class docstring 說的是相反的,而且那個
  才對:`uid_base + xxhash(item_id) % uid_range`,每個 pod 一樣、永不回收。沒有下一個
  租戶要防,只剩上面那個窗口要開,所以整個 override 直接刪掉。
- ⚠️ **降級一條 lint / type 規則,要降在債務所在的範圍。** P28 把 host 的
  `invalid-argument-type` 整包降成 `ignore`,但 51 條診斷**全部**在 `tests`,`src` 是零 ——
  於是在 `src` 注入一個真正型別錯誤的呼叫,gate 照樣全綠。那正是 P28 本身要修的
  「綠在零檔案上」,只是換了個範圍。用 `[[tool.ty.overrides]] include = ["src/**"]`
  把降級關在 `tests` 裡,兩個方向都驗過。
  ⚠️ 並且:讓 `ty` 不再往上走到 repo 根設定的,是**這裡有任何一張 `[tool.ty*]` 表**,
  不是 `exclude = []`。原本的註解把功勞掛錯了 —— 實測:單獨刪掉哪一半都照樣檢查全部檔案。
- ⚠️ **「這個旋鈕在這裡沒作用」的警告,要裝在每一個不能兌現它的入口。** 第一版只寫給
  `kind: http`;`kind: docker` 和**套了 userns jail 的 `kind: local`**(在支援 userns 但
  沒有 CAP_SETUID 的機器上就是 auto 預設)一樣沒作用、一樣安靜。而且判斷「這份有沒有
  套 jail」要**問 backend**(`keeps_item_uv_caches`),不要在 factory 再推一次 ——
  `isolate=None` 是在 backend 裡解析的,抄第二份就是第二條會跟第一條吵架的規則。
- ⚠️ **沒設上限時,連「該淘汰誰」都不要問。** 跨 pod 檢查是「每個 cache 目錄一次
  specstar 讀取,每跳一次」,而沒有上限時什麼都不會被淘汰、`.uv-cache` 又會每個 item
  長一格 —— 所以**問這件事的成本,恰好在答案不可能有用的那個設定下無上限成長**。
- ⚠️ **活性的答案,要在「動手的那一刻」才算數。** 跨 pod 檢查是每個候選一次循序 await,
  後面 backend 還要自己走一次目錄,所以第一個答案在真正刪除時已經舊了(review 實測
  426 ms 的窗口,而且刪掉了另一個 pod 已經開始填的 cache)。本 pod 那一半只是查個
  dict,所以在 sweep 前重讀一次。
- ⚠️ **`contextlib.suppress` 活下來了,但沒說話。** 它抄的兩個前例(`kill_idle` /
  `mirror_warm`)都有 `logger.warning(..., exc_info=True)`。而現在會從那裡拋出來的是
  specstar 呼叫 —— 它掛掉就等於上限無限期停止套用,而且「超過上限」那句警告蓋不到,
  因為它只在 sweep **跑完**時才印。
- ⚠️ **一個會丟掉位元組的 sink,不是「沒有 sink」。** 非串流那條路把
  `ctx.on_exec_output` 設成 `lambda b: None` —— 對 exec 輸出的描述是誠實的,但這個屬性
  是整個 app 用來問「有沒有人看得到」的,所以 `uv lock --check` 每輪都多跑一次沙盒指令、
  把答案寫進黑洞。設 `None` 就好,那本來就是 KB turn 一直在用的值。

## 為什麼 venv 不放在 workspace 裡

mirror **刻意不持久化** `.venv`(和 `node_modules/` 一起),但它**會算進使用者的
workspace 額度**。放在 workspace 裡等於**收使用者的錢、卻不保證東西還在,而且他連刪
都刪不掉**。infra 區這個模式本來就存在,不必發明新東西。
