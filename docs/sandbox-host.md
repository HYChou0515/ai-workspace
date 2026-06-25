# HTTP Sandbox host (#60)

Run the agent's sandboxes in a **separate pod** instead of the app process. The
app uses a thin `HttpSandbox` client; a self-hosted **sandbox host** service runs
the commands. Deploy the host on its own Deployment/HPA and the app reaches it
over an in-cluster Service.

See `docs/plan-http-sandbox.md` for the full design and rationale.

## Pieces

- **`HttpSandbox`** (`sandbox/http_client.py`) — the 4th `Sandbox` backend
  (peer of Local/Docker/Mock). Marshals the 12 protocol methods over HTTP;
  `exec` streams NDJSON (live output + separated stdout/stderr); files go as raw
  octet-stream. The opaque handle encodes the owning pod's URL, so any app
  replica routes correctly with no shared state. A dead pod surfaces as
  `SandboxNotFound`, and the app recreates the sandbox from the FileStore.
- **The host** (`python -m workspace_app.sandbox_host`) — a backend-agnostic
  FastAPI shell wrapping one `IsolatedProcessSandbox`. Each handle runs as a
  pooled numeric **uid/gid** (`setpriv` privilege drop) with the workspace
  `chmod 700` + a default POSIX ACL, under a per-handle **cgroup v2**
  (`memory.max` / `cpu.max` / `pids.max`). No namespaces/jail — sandboxes still
  can't read, signal, or starve each other.

## Configure

App side (the client):

```yaml
sandbox:
  kind: http
  http:
    base_url: http://sandbox-host:8000   # the host's ClusterIP Service
    read_timeout: 0                       # 0 = no HTTP read deadline; the host's
                                          # exec/log timeout bounds a long command
```

Host side (only the host process reads this):

```yaml
sandbox_host:
  bind: 0.0.0.0:8000
  uid_min: 100000        # per-handle uid/gid pool
  uid_max: 199999
  memory_max: 512M       # per-sandbox cgroup caps
  cpu_cores: 1.0
  pids_max: 512
  cgroup_root: null      # null = this pod's own delegated cgroup v2 subtree
  idle_ttl: 1800.0       # reap sandboxes orphaned by an app-pod crash (0 = off)
```

## Deploy

`deploy/sandbox-host.example.yaml` is a starting point: Deployment (same image,
`command: python -m workspace_app.sandbox_host`, `POD_IP` via the downward API,
`runAsUser: 0`), a ClusterIP Service, an HPA, a PreStop `POST /drain` hook with
`terminationGracePeriodSeconds`, `/readyz` + `/healthz` probes, and a
NetworkPolicy restricting ingress to the app pods (there is **no app-level auth**
in v1 — the host trusts in-namespace callers).

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
  (stdin=`/dev/null`, `TERM=dumb`); a spinner is bounded by the cpu cap + idle
  timeout. Humans edit via the IDE, not a terminal.
