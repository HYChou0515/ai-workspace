# Sandbox host — HTTP wire contract

這是 workspace app 的 `HttpSandbox` client
(`src/workspace_app/sandbox/http_client.py`)與獨立的 **sandbox-host**
服務(`sandbox-host/`)之間的 **contract**。兩者**不共用任何 Python 模組**——只共用這份 wire
API。app 在這裡**定義**它;host 則獨立**實作**它(#251)。

兩側都各自把關以確保一致:

- **App 側** —— `tests/sandbox/test_http.py` 拿 `HttpSandbox` 去打一個 in-test 的 fake host,
  該 fake host 比照這份 contract(也就是 app 對它的參照基準)。
- **Host 側** —— `sandbox-host/tests/test_wire.py` 在行程內(in-process)驅動真正的 server,
  而 `sandbox-host/tests/test_contract.py`(integration)則透過 subprocess 走真正的 HTTP
  去驅動它。

當你改動下面任何一處,**兩側都要一起更新**。

> 這裡只定義**線上格式**。哪一個 endpoint 在什麼時機被誰呼叫,見
> [Hosted Sandbox 執行時架構](hosted-sandbox.md)。

## Routing

`POST /sandboxes` 打到 host 的 ClusterIP Service(會做負載平衡)。回應裡帶著被選中那個 pod
自己、可直接定址的 URL(`pod_url`),外加它在本機的 handle id(`remote_id`)。client 把這兩者
打包進一個不透明的 `SandboxHandle.id`(`{"u": pod_url, "r": remote_id}` 的 base64),之後每一次
呼叫都**直接連到擁有它的那個 pod**——所以不管哪個 app replica 都能正確路由、無需共用狀態。
某個 pod 死掉(connection refused)時會被當成 `SandboxNotFound`,app 會從 FileStore 重新建立
sandbox。

## Endpoints

| Method & path | Body / params | Success | Purpose |
|---|---|---|---|
| `POST /sandboxes` | `{image?, env?, exposed_ports?, item_id?}` | `200 {pod_url, remote_id}` | 建立 |
| `DELETE /sandboxes/{rid}` | — | `204` | 終止 |
| `POST /sandboxes/{rid}/exec` | `{cmd: [str], env?: {str: str}}` | `200` NDJSON stream | exec(見下) |
| `POST /sandboxes/{rid}/persist` | `{delete: bool}` | `204` | rsync 工作目錄 → NFS 封存(#492) |
| `PUT /sandboxes/{rid}/file?path=` | raw octet-stream body | `204` | 上傳 |
| `GET /sandboxes/{rid}/file?path=` | — | `200` octet-stream | 下載 |
| `GET /sandboxes/{rid}/exists?path=` | — | `200 {exists: bool}` | 存在性檢查 |
| `GET /sandboxes/{rid}/disk-usage` | — | `200 {bytes: int}` | workspace 總用量(配額) |
| `GET /sandboxes/{rid}/size?path=` | — | `200 {size: int\|null}` | 單檔大小(配額;不存在回 `null`) |
| `POST /sandboxes/{rid}/mark-ready` | — | `204` | 標記沙盒「已完整還原、可信」(#366) |
| `GET /sandboxes/{rid}/ready` | — | `200 {ready: bool}` | 讀 ready 狀態(#366) |
| `GET /sandboxes/{rid}/walk?root=` | — | `200 {entries: [{path,size,version}]}` | walk |
| `DELETE /sandboxes/{rid}/file?path=` | — | `204` | 刪除 |
| `POST /sandboxes/{rid}/mkdir` | `{path}` | `204` | mkdir |
| `DELETE /sandboxes/{rid}/dir?path=` | — | `204` | rmdir |
| `POST /sandboxes/{rid}/rename` | `{src, dst}` | `204` | rename |
| `POST /tools/resolve` | `{tools: {名稱: manifest 網址}}` | `200 {tools: {名稱: {sha, version, stale, commands, author?, env?}}, refused: {名稱: 原因}}` | 第三方工具:抓→驗→裝,並回傳要掛的 sha、要給模型的 schema(#674)、發布者(#724)與它說自己需要的環境變數(#750) |

**`GET /sandboxes` —— app 唯一能問「現在到底有什麼」的地方**:回
`{sandboxes: [{remote_id, item_id, pod_url}]}`。`item_id` 是 app 唯一認得的名字
(沒帶 item 建的沙盒回 `null`);`pod_url` 跟 `POST /sandboxes` 回的是同一個東西,**不可省略**
——Service 會做負載平衡,這份答案是**回答的那個 pod** 的,之後要對某個沙盒做任何事都得打回那個
pod,少了它 app 只能看著孤兒卻殺不掉。

在它之前,app 保存的每一樣東西都是「某個過去時刻寫下來的信念」——計費用的心跳、路由用的位址、
面板上那顆 Close 按鈕——而**沒有任何方法拿它們去對現實**。一筆過期的紀錄跟一筆真的紀錄長得
一模一樣,於是「清掉紀錄」變成表達「它不在了」的唯一手段,包括它其實還在的時候。這正是
「關閉回報成功但 sandbox 還在跑」以及「清掉心跳等於告訴每個 replica 的 reaper:那個有人正在
用的目錄是閒置的」的共同來源。

資料源是 `_last_active`——idle reaper 走的同一張表,所以這份清單不可能漏掉 reaper 還看得到的沙盒。
item 名字在旁邊查(`_item_of`),而 `item_id` 現在**每次 create 都記**(以前只在接了 NFS archive
時才記,因為當時唯一的讀者是 `persist`)。

**這份答案是「存在的證據」,不是「不存在的證明」**:host 是多 replica,列表只涵蓋接下這次請求的
那一個 pod。要判定**某一個**沙盒沒了,只能拿它自己的 handle 去探(那會直接打到擁有它的 pod)。

維運用(不屬於 sandbox 表面)：`GET /healthz`(回
`{status, version, capabilities: [str], defaults: {cpu_cores, memory_bytes}}`——能力名與行為同
commit,不會像手維護的相容性表那樣漂移)、`GET /readyz`、`POST /drain`。

**`defaults` —— 額度的分子從哪裡來**:`create` 收到 `cpu_cores: null` 時 host 會套自己的
`SANDBOX_HOST_*`,而那是 app **讀不到**的另一個服務的環境變數。app 要向 item 的 owner 計費
「這個沙盒佔了多少」,只讀請求的話,沒宣告資源的 App 就等於免費佔著一顆核心(`/my-resources`
會在活著的環境旁邊顯示 CPU 0,per-user 的 cpu/memory 上限也永遠加總成 0 而不會生效)。所以
host 公告它實際會套的天花板,由 **enforcer 自己回答**(問 sandbox,不是重讀一次 settings),
兩邊因此不可能漂移。能力名是 `resource-defaults`;沒有它的舊 host,app 維持今天的行為(照請求
計費 ⇒ 少算),並由 `SandboxHostCapabilityCheck` 指出映像過舊——**是看得見的少算,不是猜一個
數字**。

**`item_id` + `persist`(#492)**:host 設了 `SANDBOX_HOST_NFS_ROOT` 時,帶 `item_id` 的
`create` 會**先**把 `{nfs_root}/{item_id}` rsync 還原進新沙盒、`reown` 成沙盒 uid、最後才
`mark-ready`(所以 `create` 一回來,目錄就是完整且可信的);`persist` 再把它 rsync 回去——
`delete: true` 是靜止點的**對帳**,`false` 是回合中的**純追加** checkpoint,且**只在 ready 為真
時**執行(半還原的目錄絕不能覆蓋封存)。沒有 archive 或沒帶 `item_id` ⇒ 兩者都是 no-op,舊
client 因此照舊可用。

### `POST /tools/resolve` —— 為什麼回應是「部分成功」

回應**刻意不是全有全無**:每個工具各自成功或被拒(`refused` 逐項給原因),
app 收到後把失敗的那支拿掉、turn 照跑。若整個請求 500,一個作者過期的 artifact
就會**連帶讓同一個 workspace 裡其他所有工具消失**——那是營運上最糟的失敗形狀。

**`author` 與 `env` 是選填的,而且「沒有這個鍵」和「空的」意思不同。** app **永遠不會自己讀
manifest**(那是這條路存在的理由),所以這個回應丟掉什麼,app 就永遠拿不到什麼。

- `author`(#724):發布者字串,沒有就是這份 bundle 建於該欄位存在之前。
- `env`(#750):作者宣告這支工具需要哪些環境變數,`[{name, description, required}]`。
  **鍵不存在 = 作者沒講**(#750 之前發布的 artifact 都是這樣);**空陣列 = 作者看過而且不需要**。
  兩者必須都能過線:平台把前者顯示成「這個工具沒有列出它需要什麼」,把後者顯示成「不需要」,
  而使用者打開那個面板正是為了知道自己還缺什麼——把沉默講成「不需要」是它最不該說的一句話。
  `required` 同樣是三態,`null` 代表作者沒標,既不是必填也不是選填。

回應同時帶 `sha`(sandbox 要掛哪一份)與 `commands`(要告訴模型這支工具吃什麼參數)。
**兩者出自同一次 resolve**,所以 app 眼中的介面與 sandbox 裡實際跑的 bundle 不可能對不上;
若 app 自己另外去讀 manifest,作者在兩次讀取之間發版就會讓模型用上一版的參數去呼叫新版工具。

`stale: true` 代表 artifact store 連不上、這是**上一次成功解析**的版本;
工具仍可用,但 app 應該讓使用者知道它不是最新的。

檔案以 **raw `application/octet-stream`** 的 body 傳遞(不是 base64-in-JSON)。
路徑都是相對於 workspace root;開頭的 `/` 代表 workspace root。

**Readiness marker(#366)**:`mark-ready`/`ready` 操作的是一個放在**沙盒根、workspace
外**的空檔(`$root/{id}/.ready`,跟 workspace 平輩),所以它**不會**出現在 `walk`、檔案樹或
`exists`,使用者也無法用同名檔偽造。app 的 mirror 只有在 `ready` 為真(walk 前後各驗一次)時才
傳播刪除;沙盒回收(`DELETE /sandboxes/{rid}`)會**先**移除這個 marker 再 rmtree。

這裡**沒有 `expose_port` endpoint**——v1 沒有 sandbox 內網路服務的路徑。client 的
`expose_port` 會丟 `NotImplementedError`。`upload_file` /
`download_to_file` 是 client 端對 `PUT`/`GET /file` 的便利封裝,不是獨立的 endpoint。

**`exec` 的 `env`**:這個指令要看到的**額外**環境變數,由呼叫端逐次指名(#673)。
呼叫端的值**最後套用**,所以蓋得過 exec 路徑自己設的東西。省略即可——舊 client 不送這個欄位,
host 把「沒送」和「空的」視為同一件事。

> 這取代了早期把變數寫成一個沙盒內檔案、再讓工具去讀的做法:同一個 sandbox 裡 agent 和工具
> 共用 uid,落在磁碟上的東西**兩者都讀得到**。逐次指名之後,agent 自己的 `exec` 沒有東西可繼承、
> 也沒有檔案可打開。

## `exec` —— NDJSON streaming

回應是 `application/x-ndjson`,一行一個 JSON 物件:

- `{"o": "<base64>"}` —— 一個即時輸出的 chunk(stdout+stderr 交錯),一到就送出;
  client 會把解碼後的 bytes 轉發到它的 `on_output` sink。
- 最後一個 frame `{"exit": int, "out": "<base64>", "err": "<base64>"}` —— exit
  code 加上**分開的**完整 stdout/stderr 緩衝區,client 據此重建 `ExecResult`。
- `{"error": "<type>", "detail": "<msg>"}` —— 若 `exec` 在 host 上拋了例外。此時
  HTTP 狀態已經是 `200`(stream 已開啟),所以後端錯誤是**帶內**(in-band)以一個 frame
  傳遞;client 會重新拋出對映到的例外。
- 若 stream 在最後一個 `exit`/`error` frame **之前**就結束,client 會把它當成
  pod 死掉 → `SandboxNotFound`(任何已送達的 `o` chunk 都保留)。

## Error model

帶 body `{"error": "<type>", "detail": "<msg>"}` 的 `404` 會對映回 client 拋出的
例外:

- `SandboxNotFound` —— 未知 / 已終止的 handle,或某個死掉的 pod(連線錯誤)。
- `FileNotFoundError` —— 下載 / 刪除 / rmdir / rename 時檔案不存在。

指令的非零 exit **不是**錯誤——它搭著 exec 的 `exit` frame 一起回來。

**傳輸層的兩種壞法必須分開(#492)**:**逾時**代表 pod 可達但**慢**(過載 / 大檔傳輸中)
⇒ client 映成 `SandboxBusy`,沙盒**還活著**,冪等的檔案/探測操作會以**遞增的讀取期限 + 遞增
退避**(皆有上限)重試,超過次數就大聲失敗;把「只是忙」誤判成死會再開一個沙盒 = split-brain。
其他傳輸失敗(連線被拒/重設)才是 `SandboxNotFound`。`create`(不冪等)、`persist`(長時間
rsync)、`exec`(有自己的期限)**一律不重試**。

## Auth

`GET /sandboxes` 是唯一一支**列舉**的端點:不需要先知道任何 `remote_id`,一次就吐出這個 pod 上
所有租戶的 item id。其餘端點都要先有 handle。在下面這個「namespace 內全開」的模型下這不改變
結論,但哪天要上 auth,這支是門檻最低的那一支。

v1 沒有:host 只在 cluster namespace 內可達
(NetworkPolicy / ClusterIP)。任何 namespace 內的 caller 都能驅動它——這是可接受的。
