# HTTP Sandbox host (#60, #251)

Run the agent's sandboxes in a **separate service** instead of the app process.
The app uses a thin `HttpSandbox` client; a self-hosted **sandbox host** runs the
commands. Deploy the host on its own Deployment/HPA and the app reaches it over
an in-cluster Service.

Since #251 the host is a **fully standalone project** — `sandbox-host/`, with its
own `pyproject.toml` + `uv.lock` + image, sharing **no Python modules and no
dependencies** with the app. Its only coupling to the app is the HTTP **wire
contract** (`docs/sandbox-host-wire.md`), which the app defines and the host
implements independently. See `docs/plan-http-sandbox.md` for the original design.

## Pieces

- **`HttpSandbox`** (app: `src/workspace_app/sandbox/http_client.py`) — the 4th
  `Sandbox` backend (peer of Local/Docker/Mock). Adapts the wire API to the app's
  `Sandbox` protocol; `exec` streams NDJSON (live output + separated
  stdout/stderr); files go as raw octet-stream. The opaque handle encodes the
  owning pod's URL, so any app replica routes correctly with no shared state. A
  dead pod surfaces as `SandboxNotFound`, and the app recreates the sandbox from
  the FileStore.
- **The host** (`python -m sandbox_host`, from `sandbox-host/`) — a
  FastAPI shell wrapping one `IsolatedProcessSandbox`. Each handle runs as a
  pooled numeric **uid/gid** (`setpriv` privilege drop) with the workspace
  `chmod 700` + a default POSIX ACL, under a per-handle **cgroup v2**
  (`memory.max` / `cpu.max` / `pids.max`). No namespaces/jail — sandboxes still
  can't read, signal, or starve each other. Runtime deps: just fastapi / uvicorn
  / pydantic (+ `util-linux`/`acl` for setpriv/setfacl) — none of the app's
  LLM/KB/data stack.

## Configure

App side (the client) — in the app's config:

```yaml
sandbox:
  kind: http
  http:
    base_url: http://sandbox-host:8000   # the host's ClusterIP Service
    read_timeout: 0                       # 0 = no HTTP read deadline; the host's
                                          # exec/log timeout bounds a long command
```

Host side — **environment variables** (`SANDBOX_HOST_*`), set on the host pod
(no shared config file):

| Env var | Default | Meaning |
|---|---|---|
| `SANDBOX_HOST_BIND` | `0.0.0.0:8000` | listen address |
| `SANDBOX_HOST_UID_MIN` / `_UID_MAX` | `100000` / `199999` | per-handle uid/gid pool |
| `SANDBOX_HOST_MEMORY_MAX` | `512M` | per-sandbox cgroup `memory.max` |
| `SANDBOX_HOST_CPU_CORES` | `1.0` | per-sandbox cgroup `cpu.max` |
| `SANDBOX_HOST_PIDS_MAX` | `512` | per-sandbox cgroup `pids.max` |
| `SANDBOX_HOST_CGROUP_ROOT` | _(unset)_ | delegated cgroup v2 subtree; unset = auto-detect |
| `SANDBOX_HOST_TOOLS_DIR` | _(unset)_ | prebuilt-tools dir bind-mounted at `/.tools`; unset = no tools |
| `SANDBOX_HOST_IDLE_TTL` | `1800` | reap sandboxes orphaned by an app-pod crash; 0 = off |

(`SANDBOX_HOST_ROOT`, `_EXEC_TIMEOUT`, `_LOG_TIMEOUT` also exist — see
`sandbox-host/src/sandbox_host/config.py`.)

## Tool delivery (#251)

The agent's prebuilt tools (the `python-stack` data-science carrier,
`data-fetch`, etc.) must be present **inside** the sandbox or `exec(["python",
…])` and the tool commands fail. The host bind-mounts `SANDBOX_HOST_TOOLS_DIR`
read-only at `/.tools` in every sandbox and treats it as an **opaque directory**
— it never imports the app's tool registry.

`sandbox-host/Dockerfile` bakes the tools in: a throwaway build stage runs the
app's `scripts/prebuild_tools.py` (which needs `workspace_app` + `sample-tools`),
and the runtime stage copies only the resulting self-contained bundles to
`/opt/tools`, with `SANDBOX_HOST_TOOLS_DIR=/opt/tools` set by default. So the
runtime image stays lean while the tools ride along. The app↔host tool set is
kept in sync by **convention** (same prebuilt artifact) — there is no
cross-import check; the host logs `tools_dir` at boot for visibility.

> Before #251 the host never wired `tools_dir` at all, so http-sandbox agents
> silently had **no** prebuilt tools. That is the bug this fixes.

## Deploy

`deploy/sandbox-host.example.yaml` is a starting point: Deployment
(`image: sandbox-host:latest`, `command: python -m sandbox_host`, `POD_IP` via the
downward API, `SANDBOX_HOST_*` env, `runAsUser: 0`), a ClusterIP Service, an HPA,
a PreStop `POST /drain` hook with `terminationGracePeriodSeconds`, `/readyz` +
`/healthz` probes, and a NetworkPolicy restricting ingress to the app pods (there
is **no app-level auth** in v1 — the host trusts in-namespace callers).

Build the image from the repo root:
`docker build -t sandbox-host:latest -f sandbox-host/Dockerfile .`

## Requirements & limits

- **Root + cgroup v2 delegation.** The host setuids/chowns to foreign uids and
  writes a delegated cgroup subtree, so it runs as root and **fails loud at boot
  / `/readyz`** if cgroup v2 isn't mounted or the subtree isn't writable.
  Delegation is runtime-specific (often needs `Delegate=yes` or a privileged
  container on managed nodes).
- **No namespaces.** PID list and network are shared across sandboxes on a pod
  (cross-uid *kill/ptrace* and *file read* are still blocked); `/tmp` is mapped
  per-handle via `TMPDIR`. For stronger isolation, run fewer sandboxes per pod.
- **`expose_port` is unsupported in v1** (no in-sandbox network-service path).
- Interactive TUIs (`vim`, `top`) aren't supported — `exec` is one-shot
  (stdin=`/dev/null`); a spinner is bounded by the cpu cap + idle timeout. Humans
  edit via the IDE, not a terminal.
