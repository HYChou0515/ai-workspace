# Plan：使用者環境變數改由 exec 注入（取代 `.userenv` 檔案）

接續 #664。這份計畫要修的是**一個回報的 bug** 和**一個設計缺口**,而它們有**同一個根因**。

## 現況與問題

#664 的投遞方式是:app 每回合把值寫成 `KEY=VALUE` 檔案放進 sandbox 的 infra area
(`$root/<id>/.userenv`),`_exec_argv` 對**每一次** exec 把路徑放進 `SANDBOX_USER_ENV`,
再由 **launcher 腳本**在 `exec` 前最後一行逐行讀檔並 `export`。

**回報的 bug**

```
jailbin/python: 54: cannot open .userenv: Permission denied
```

launcher 的 guard 是 `[ -f "$SANDBOX_USER_ENV" ]`——`-f` 只判斷「存在且是普通檔」,
**不判斷可讀**。所以檔案在、guard 過關,下一步的 `done < "$SANDBOX_USER_ENV"` 直接失敗。

⚠️ **這不是「正確地擋住 AI」。** uid 由 `_derive_uid(item_id)` 導出,綁 item 不綁呼叫者——
**tool 和 agent 跑在同一個 uid**。所以讀不到不是只有 `exec python` 讀不到,**真正的 tool 也讀不到**,
功能在該部署上等於失效。

**設計缺口**

正因為同一個 uid,「tool 讀得到、AI 讀不到」用**檔案**這個載體**在原理上就做不到**:權限分不開,
要嘛都讀得到、要嘛都讀不到。而 `_exec_argv` 又無條件把路徑給每一次 exec,所以 agent 隨時可以
`exec sh -c 'cat "$SANDBOX_USER_ENV"'` 全部讀走。

## 決定的做法

**值不落地。** 由 app 在**工具派送那一刻**把值放進該次 exec 的環境,經由 `exec` 協定傳遞:

```
item.env_vars（唯一真相）
  → tooling/registry 的 tool 派送   ← 唯一注入點
  → sandbox.exec(..., env=...)
  → local_process 併進 _exec_argv 的 env
  → setpriv 降權（無 --reset-env，環境保留；SANDBOX_HOME 已證實走得通）
  → 工具拿到
```

hosted 在中間多一段:`http_client` 把 env 放進 POST body → sandbox-host 的 exec route → 同樣併入。

**一次解掉三件事**:沒有檔案(權限問題消失)、沒有 launcher 解析(那段 shell 整段刪掉)、
agent 自己的 `exec`/`python` 天生就沒有(只有工具派送那次會帶)。

寫入端與 exec 端**本來就是特權身分**,所以「誰有權限讀」這個問題從一開始就不存在。

### 保留字順序：必須顯式保住

⚠️ 這是本計畫**最容易靜默做錯**的一點。

launcher 自己會改環境(行號為 `_LAUNCH` 模板內):

```
 9  export HOME="${SANDBOX_HOME:-...}"
20  export PIP_USER=1
29  export PYTHONPATH="$mine:$bundled${PYTHONPATH:+:$PYTHONPATH}"
54  exec ...
```

現在使用者的變數插在 **29 和 54 之間**,腳本改完才輪到它 → **使用者贏**(#664 的既定決策:
保留字放行,使用者自負風險)。

改成初始環境傳入後,值在腳本啟動**前**就存在,第 9/20/29 行接著把它覆寫 → **carrier 贏**,
也就是「存了、列得出來、卻沒作用」——正是那個順序當初要避免的失敗。
**這跟用 `env=` 還是 `FOO=BAR ./launch` 無關,純粹是「誰最後寫」。**

**解法**:app 額外帶一個 `SANDBOX_USER_ENV_KEYS="A B C"`,launcher 在 `exec` 前把這些**名字**
從自己的環境裡重新 `export` 一次。不需要檔案、不需要解析值,順序回到現行行為。

## 已鎖定的決策

1. **skill 的 `scripts/` 不再拿得到**——它們走 `exec python`(python-stack carrier)。
   使用者已確認接受。這是本計畫唯一真的少掉的能力。
2. **保留字仍然是使用者贏**——靠 `SANDBOX_USER_ENV_KEYS` 重新 export 保住,不接受退化。
3. **`.userenv` 檔案與 `write_user_env` 協定 op 整個拆除**,不留相容路徑
   (兩套規則並存 = 保證其中一套變假)。

## 知情取捨（不需再決策，但要寫進 PR）

- **值每次工具呼叫過一次 HTTP body**(現行是一回合一次)。同一條內部連線,無安全差別。
- **同 uid 仍可在工具執行當下讀 `/proc/<pid>/environ`**。要做到需先留一個背景輪詢程式,
  且窗口只有工具存活的那幾秒——比現行「檔案隨時可 `cat`」窄很多,但**不是零**。
  要真的堵死只能讓 tool 與 agent 用不同 uid,那是另一個層級的改動,不在本計畫。

## Phases

每個 phase 一個 commit,可 bisect。

### Phase 1 — `exec` 協定接受 `env`

`sandbox/protocol.py` 的 `exec` 加 `env: Mapping[str, str] | None = None`;
`local_process` 併進 `_exec_argv` 產生的 env(`isolated_process` 繼承,不需改);
`mock` / `docker` 同步接受並記錄。**替身也要改**——替身跟真實不一致正是 #492 的教訓。

紅燈測試:對 mock 與真 `LocalProcessSandbox` 各斷言「傳進去的 env 出現在子行程」。

### Phase 2 — HTTP 這一跳把 env 帶過去

`http_client.exec` 的 `json={"cmd": cmd}` 加上 env;`sandbox-host` 的
`protocol` / `app.py` 的 exec route / `local_process` 對應接收併入(`isolated_process` 繼承)。

⚠️ **production 跑的是 sandbox-host 那份**——只改 app 側會全綠卻沒功能(#664 踩過)。
紅燈測試要跨過這一跳,不能只測 app 側。

### Phase 3 — 工具派送注入

`tooling/registry.py` 有**兩處**會 exec 一個 tool launch:

- `on_invoke`(工具派送)
- `_review_chart` 內的 `render()`(#285 圖表重繪,重跑同一個 command)

兩處收斂成一個 helper,env 只在那裡帶入 → 唯一注入點的性質保住。

同時帶 `SANDBOX_USER_ENV_KEYS`,並在兩個 launcher 模板的 `exec` 前加上「依名字重新 export」
那一小段(取代現行的讀檔迴圈)。

紅燈測試:使用者設 `PIP_USER=0` 時,工具實際收到的是 `0` 而非 carrier 的 `1`。

### Phase 4 — 拆除 `.userenv`

刪 `write_user_env`(protocol / local_process / isolated_process / http_client / docker / mock,
app 側與 sandbox-host 側各一份)、`_USER_ENV` 常數、`_exec_argv` 的 `SANDBOX_USER_ENV`、
launcher 模板裡的讀檔迴圈、`agent/context.py` 的呼叫,以及
`tests/sandbox/test_user_env.py` 等對應測試。

驗收:全 repo `grep -r "\.userenv\|write_user_env\|SANDBOX_USER_ENV\b"` 只剩
`SANDBOX_USER_ENV_KEYS`。

### Phase 5 — 文件對齊

`docs/extending-the-platform.md` 的「讀使用者設的環境變數」整段改寫——**套用範圍表會反轉**:

| 執行路徑 | 現行 | 本計畫後 |
|---|---|---|
| 註冊的 tool | ✅ | ✅ |
| `exec` 裡的 `python` / `python3*` | ✅ | ❌ |
| `exec` 裡的其他指令 | ❌ | ❌ |
| agent 刻意 `cat` 那個檔 | ✅ 讀得到 | ❌ 沒有檔案 |

⚠️ PR #669 記錄的是**現行**行為,本計畫落地後那份文件即過時,必須在同一批更新。

## 驗收

- 上述每個 phase 的紅燈測試先紅後綠(「revert 這行,哪條**新增**測試會紅」要答得出來)。
- 端到端:真 `LocalProcessSandbox` + 真模板建的 stub bundle 跑真 subprocess,驗
  「item.env_vars → exec env → launcher → 工具的 `os.environ`」整條鏈。
  兩半各自的單元測試就算互相不同意也會全綠,那條縫才是 bug 會出現的地方。
- 回歸判準是**與 master 的失敗清單差集**,不是絕對數字。
