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
| `POST /sandboxes` | `{image?, env?, exposed_ports?}` | `200 {pod_url, remote_id}` | 建立 |
| `DELETE /sandboxes/{rid}` | — | `204` | 終止 |
| `POST /sandboxes/{rid}/exec` | `{cmd: [str]}` | `200` NDJSON stream | exec(見下) |
| `PUT /sandboxes/{rid}/file?path=` | raw octet-stream body | `204` | 上傳 |
| `GET /sandboxes/{rid}/file?path=` | — | `200` octet-stream | 下載 |
| `GET /sandboxes/{rid}/exists?path=` | — | `200 {exists: bool}` | 存在性檢查 |
| `GET /sandboxes/{rid}/walk?root=` | — | `200 {entries: [{path,size,version}]}` | walk |
| `DELETE /sandboxes/{rid}/file?path=` | — | `204` | 刪除 |
| `POST /sandboxes/{rid}/mkdir` | `{path}` | `204` | mkdir |
| `DELETE /sandboxes/{rid}/dir?path=` | — | `204` | rmdir |
| `POST /sandboxes/{rid}/rename` | `{src, dst}` | `204` | rename |

維運用(不屬於 sandbox 表面)：`GET /healthz`、`GET /readyz`、
`POST /drain`。

檔案以 **raw `application/octet-stream`** 的 body 傳遞(不是 base64-in-JSON)。
路徑都是相對於 workspace root;開頭的 `/` 代表 workspace root。

這裡**沒有 `expose_port` endpoint**——v1 沒有 sandbox 內網路服務的路徑。client 的
`expose_port` 會丟 `NotImplementedError`。`upload_file` /
`download_to_file` 是 client 端對 `PUT`/`GET /file` 的便利封裝,不是獨立的 endpoint。

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

## Auth

v1 沒有:host 只在 cluster namespace 內可達
(NetworkPolicy / ClusterIP)。任何 namespace 內的 caller 都能驅動它——這是可接受的。
