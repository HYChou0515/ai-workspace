# HTTP Sandbox host(#60, #251)

把 agent 的 sandbox(沙箱)跑在**獨立的服務**裡,而不是在 app 進程內。
app 只用一個輕量的 `HttpSandbox` client;真正執行指令的是自架的 **sandbox host**。
把 host 部署成自己的 Deployment/HPA,app 透過 in-cluster Service 連到它。

從 #251 起,host 是一個**完全獨立的專案**——`sandbox-host/`,有自己的
`pyproject.toml` + `uv.lock` + image,與 app **不共用任何 Python module、也不共用任何
相依套件**。它與 app 唯一的耦合是 HTTP **wire contract**(`docs/sandbox-host-wire.md`),
由 app 定義、host 各自獨立實作。原始設計見 `docs/plan-http-sandbox.md`。

## 組成

- **`HttpSandbox`**(app:`src/workspace_app/sandbox/http_client.py`)——第 4 種
  `Sandbox` backend(與 Local/Docker/Mock 平起平坐)。把 wire API 轉接到 app 的
  `Sandbox` protocol;`exec` 以 NDJSON 串流(即時輸出 + 分離的 stdout/stderr);
  檔案以原始 octet-stream 傳輸。那個不透明的 handle 編入擁有它的 pod 的 URL,所以
  任何 app replica 都能正確路由、不需共享狀態。pod 掛掉會以 `SandboxNotFound` 浮現,
  app 再從 FileStore 重建 sandbox。
- **host**(`python -m sandbox_host`,來自 `sandbox-host/`)——一層 FastAPI 殼,
  包住一個 `IsolatedProcessSandbox`。每個 handle 以一組池化的數字 **uid/gid** 執行
  (`setpriv` 降權),workspace 設 `chmod 700` + 一份預設 POSIX ACL,並置於每個 handle
  專屬的 **cgroup v2**(`memory.max` / `cpu.max` / `pids.max`)下。沒有 namespace/jail
  ——sandbox 之間仍無法互相讀取、發訊號或互相餓死。Runtime 相依只有 fastapi / uvicorn
  / pydantic(外加 `util-linux`/`acl` 提供 setpriv/setfacl)——完全不含 app 的
  LLM/KB/資料處理那套堆疊。

## 設定

App 端(client)——在 app 的 config 裡:

```yaml
sandbox:
  kind: http
  http:
    base_url: http://sandbox-host:8000   # host 的 ClusterIP Service
    read_timeout: 0                       # 0 = 不設 HTTP 讀取期限;由 host 的
                                          # exec/log timeout 來限制長時間指令
```

Host 端——**環境變數**(`SANDBOX_HOST_*`),設在 host pod 上
(不共用 config 檔):

| 環境變數 | 預設 | 意義 |
|---|---|---|
| `SANDBOX_HOST_BIND` | `0.0.0.0:8000` | 監聽位址 |
| `SANDBOX_HOST_UID_MIN` / `_UID_MAX` | `100000` / `199999` | 每個 handle 的 uid/gid 池 |
| `SANDBOX_HOST_MEMORY_MAX` | `512M` | 每個 sandbox 的 cgroup `memory.max` |
| `SANDBOX_HOST_CPU_CORES` | `1.0` | 每個 sandbox 的 cgroup `cpu.max` |
| `SANDBOX_HOST_PIDS_MAX` | `512` | 每個 sandbox 的 cgroup `pids.max` |
| `SANDBOX_HOST_CGROUP_ROOT` | _(未設)_ | 委派的 cgroup v2 子樹;未設 = 自動偵測 |
| `SANDBOX_HOST_TOOLS_DIR` | _(未設)_ | 預建工具目錄,bind-mount 在 `/.tools`;未設 = 無工具 |
| `SANDBOX_HOST_IDLE_TTL` | `1800` | 回收因 app-pod crash 而成孤兒的 sandbox;0 = 關閉 |

(`SANDBOX_HOST_ROOT`、`_EXEC_TIMEOUT`、`_LOG_TIMEOUT` 也存在——見
`sandbox-host/src/sandbox_host/config.py`。)

## 工具遞送(#251)

agent 的預建工具(`python-stack` 資料科學載體、`data-fetch` 等)必須存在於
sandbox **內部**,否則 `exec(["python", …])` 與工具指令都會失敗。host 把
`SANDBOX_HOST_TOOLS_DIR` 以唯讀方式 bind-mount 到每個 sandbox 的 `/.tools`,並把它
當成一個**不透明的目錄**——它從不 import app 的 tool registry。

`sandbox-host/Dockerfile` 把工具烤進去:一個用完即丟的 build stage 跑 app 的
`scripts/prebuild_tools.py`(需要 `workspace_app` + `sample-tools`),而 runtime stage
只把產出的自包含 bundle 複製到 `/opt/tools`,並預設帶上 `SANDBOX_HOST_TOOLS_DIR=/opt/tools`。
這樣 runtime image 保持精簡,工具則隨車一起到。app↔host 的工具集靠**慣例**保持同步
(同一份預建產物)——沒有跨 import 的檢查;host 開機時會記錄 `tools_dir` 以利檢視。

> #251 之前,host 根本沒有接上 `tools_dir`,所以 http-sandbox 的 agent 默默地**沒有**
> 任何預建工具。這正是本次修掉的 bug。

## 部署

`deploy/sandbox-host.example.yaml` 是一個起點:Deployment
(`image: sandbox-host:latest`、`command: python -m sandbox_host`、透過 downward API 取得
`POD_IP`、`SANDBOX_HOST_*` env、`runAsUser: 0`)、一個 ClusterIP Service、一個 HPA、
一個 PreStop 的 `POST /drain` hook 搭配 `terminationGracePeriodSeconds`、`/readyz` +
`/healthz` 探針,以及一個把 ingress 限制給 app pod 的 NetworkPolicy(v1 **沒有 app 層級的
認證**——host 信任同 namespace 內的呼叫者)。

從 repo 根目錄 build image:
`docker build -t sandbox-host:latest -f sandbox-host/Dockerfile .`

## 需求與限制

- **Root + cgroup v2 委派。** host 會 setuid/chown 到外來的 uid 並寫入一個委派的
  cgroup 子樹,所以它以 root 執行,且在 cgroup v2 未掛載或子樹不可寫時於
  **開機 / `/readyz` 時大聲失敗**。委派方式因 runtime 而異(在受管節點上通常需要
  `Delegate=yes` 或特權容器)。
- **沒有 namespace。** PID 清單與網路在同一個 pod 上的 sandbox 之間共享
  (跨 uid 的 *kill/ptrace* 與 *檔案讀取* 仍被擋住);`/tmp` 透過 `TMPDIR` 對每個 handle
  各自映射。要更強的隔離,就讓每個 pod 跑更少的 sandbox。
- **v1 不支援 `expose_port`**(沒有 sandbox 內網路服務的路徑)。
- 不支援互動式 TUI(`vim`、`top`)——`exec` 是一次性的(stdin=`/dev/null`);打轉的
  程序由 cpu cap + idle timeout 來設限。人類透過 IDE 編輯,不是透過終端機。
