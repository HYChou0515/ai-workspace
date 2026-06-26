# Plan — HTTP Sandbox (#60)

> **Superseded in part by #251:** the host described below as part of
> `workspace_app` is now a **standalone project** (`sandbox-host/`, own deps/image,
> env-based config, no shared modules). The wire contract is
> `docs/sandbox-host-wire.md`; operator docs are `docs/sandbox-host.md`. This
> document is kept for the original design rationale.

> A fourth `Sandbox` backend, **`HttpSandbox`** (the client), plus a self-hosted
> **sandbox host service** it talks to over HTTP. The host runs in its own
> pod/Deployment (later HPA), so sandbox execution is decoupled from the app
> process. Locked via `/grill-me`; build per `/tdd` (red-green-refactor) one phase
> at a time. Gate at the end with the full suite + 100% coverage (no pipe-mask),
> `ruff`, `ty`, and a **live canned check** against a real host process.
>
> **Guiding principle:** `HttpSandbox` is a *faithful HTTP wrapper of
> `LocalProcessSandbox`* — production runs `LocalProcessSandbox` today, so its
> behaviour (per-chunk streaming, `exit 124` timeout, stdout/stderr separation,
> `/`-rooted paths, `walk` version-stamps the mirror depends on) is the contract.
> `DockerSandbox`'s degradations (whole-output-at-end) are **not** a precedent.

## Why build, not adopt (rationale, recorded)

`/grill-me` surveyed the 2026 landscape (verified): **no drop-in image matches our
`Sandbox` Protocol** without coupling or KVM. microVM servers (microsandbox/libkrun,
Arrakis/cloud-hypervisor, E2B/Firecracker — self-host experimental) **all need KVM**;
gVisor/Kata are *runtimes* with no exec/file API. The only "infra does it all" path is
**k8s-as-sandbox** (`kubernetes-sigs/agent-sandbox`, `llm-sandbox` k8s backend:
pod-per-session + `pods/exec` + tar-over-exec) — but that costs **~1 s warm /
seconds-to-tens-of-seconds cold (image pull dominates)** per sandbox and needs a warm
pool at scale. Our model — **one warm host pod holding many sandboxes (processes),
`create` ≈ ms** — deliberately avoids the pod-per-session cost and keeps faithful
parity with the production `LocalProcessSandbox`. So we hand-roll a small FastAPI host
using two standard utilities (`setpriv` for the privilege drop, direct cgroup v2 fs
writes for limits) rather than adopt.

## Key existing seams to hook (don't rebuild)

- **`sandbox/protocol.py` `Sandbox`** — the 12-method contract `HttpSandbox` and the
  host both honour. Don't change it (except `HttpSandbox` documents that
  `expose_port` raises `NotImplementedError`).
- **`sandbox/local_process.py` `LocalProcessSandbox`** — `IsolatedProcessSandbox`
  *subclasses* it: inherits all 8 file ops + the `exec` pump/timeout machinery
  verbatim; the only surgical change to the production class is **extracting the
  exec argv/env construction into an overridable `_exec_argv` hook**.
- **`sandbox/mock.py` `MockSandbox`** — injected into the host in L1 unit tests so the
  whole wire round-trip runs with no isolation, no root.
- **`factories.py` `get_sandbox`** — add `case "http": return HttpSandbox(...)`.
- **`config/schema.py` `SandboxSettings`** — extend with `kind="http"` + nested
  `http`; add a new top-level `SandboxHostSettings`.
- **`config/loader.py`** — `_dataclass_keys` / `_build` wiring + `config.example.yaml`.
- **`__main__.py`** — new entrypoint `python -m workspace_app.sandbox_host`; reuse the
  boot-step narration (`boot_step` → / ✓ / ✗) and config-dump observability.
- **`api/registry.py` `InvestigationRegistry`** — *unchanged*; it already treats the
  sandbox as a warm cache of the FileStore snapshot (create → `sync.restore`, idle →
  `kill` + `sync.mirror`). A dead host pod simply looks like a cold sandbox.

---

## Locked decisions (from `/grill-me`)

### Topology
- `HttpSandbox` = 4th `Sandbox` client, peer of Local/Docker/Mock.
- Host = backend-agnostic FastAPI shell wrapping **one injected `Sandbox`**;
  production injects `IsolatedProcessSandbox`. Same repo, **same image**, new
  entrypoint `python -m workspace_app.sandbox_host`.

### Isolation (`IsolatedProcessSandbox(LocalProcessSandbox)`, `isolate=False`)
- **No namespaces/jail.** Isolation = Linux uid + cgroups (the *reason this backend
  exists* — without per-sandbox isolation + resource caps it would be no better than
  `LocalProcessSandbox`; "sandboxes must not interfere" is a hard requirement).
- **Per-handle bare numeric uid/gid** from a configured pool (`setpriv --reuid/--regid
  --clear-groups`; no `useradd`, kernel setuid to a number needs no passwd entry).
  Freed on `kill`.
- **File isolation:** `create` does `chown` + `chmod 700` on the handle workspace +
  sets a **default POSIX ACL** (`setfacl -R -m u:UID:rwx -d -m u:UID:rwx`) so files the
  host (root) later writes are automatically rwx by the handle uid (covers nested
  writes; keeps the subclass override surface = create/kill only). Fail-loud if the FS
  lacks ACL support.
- **Process isolation:** distinct uids ⇒ Linux forbids cross-handle kill/ptrace.
- **Resource isolation:** per-handle **cgroup v2** (`memory.max` / `cpu.max` /
  `pids.max`). `exec` wraps the command:
  `sh -c 'echo $$ > <cgroup>/cgroup.procs; exec setpriv … -- <cmd>'` (join cgroup,
  then drop privilege). No `preexec_fn`, no `systemd-run`.
- **Fail-loud** at host startup if cgroup v2 / delegation is unavailable (isolation is
  the whole point — never silently degrade).
- **Per-handle `TMPDIR`/`HOME`** inside the workspace (mitigates shared `/tmp`).
- **Accepted v1 residuals** (no namespaces): shared PID view (but cross-uid kill is
  blocked) + shared network. Cross-handle file/process/resource interference is closed.

### Interactive / TTY programs (vim, top, REPL) — option A
- `exec` stays one-shot, non-interactive (its caller is the LLM agent; humans edit via
  the IDE, not a terminal). **No PTY.** `stdin=/dev/null` (EOF) + `TERM=dumb` make
  almost every TUI exit promptly; `cgroup cpu.max` + idle `log_timeout` +
  process-group SIGKILL + uid isolation are the backstop for any spinner. A real web
  terminal (PTY + WebSocket + xterm.js) is a separate future feature, out of #60.

### Routing (HPA-ready, stateless)
- `create` hits the host **ClusterIP Service**; the chosen pod reports its **own direct
  URL** (downward-API `POD_IP`) + its local remote-id. `HttpSandbox` **encodes
  `(pod_url, remote_id)` into the opaque `SandboxHandle.id`** (it owns the id format;
  the app treats it opaque). Every other method **decodes → connects direct to that
  pod** (bypassing the LB). → app side fully stateless, HPA-safe, no shared store, no
  sticky-routing dependency.
- Host pod death (scale-down/crash) → direct call fails → mapped to `SandboxNotFound`
  → `InvestigationRegistry` recreates from the snapshot (same as today's idle-kill cold
  path; loses only in-sandbox ephemeral state). Mitigate with PreStop drain +
  conservative scale-down.

### Wire / API
- One endpoint per protocol method (REST-ish, boring). Files = **raw
  `application/octet-stream`** body (no base64-in-JSON); metadata (walk) = JSON.
- `exec` = **NDJSON streaming**: one line per chunk `{"s":"out|err","b":"<base64>"}`,
  final line `{"exit":N}`. Client forwards each chunk to `on_output`, buckets out/err
  separately, rebuilds `ExecResult`. (Preserves `LocalProcessSandbox` per-chunk
  streaming — the production behaviour.)
- Client **read-timeout very large / disabled**; the host's `exec_timeout` +
  `log_timeout` are the real bounds.
- `expose_port` → **`NotImplementedError`** (verified zero production callers; no
  Jupyter / in-sandbox network consumer); `exposed_ports` ignored.
- **No authentication** — host is reachable only inside the k8s namespace
  (NetworkPolicy / ClusterIP). Residual (any in-namespace service can drive the host)
  accepted.
- **Error model:** host returns a structured error (HTTP status + `{type}`); client
  maps `type` → `SandboxNotFound` / `FileNotFoundError` / `NotImplementedError`.
  Connection failure / dead pod → `SandboxNotFound`.
- `spec.image` ignored (no containers); `spec.env` forwarded to `exec`. uid pool +
  handle map guarded by an async lock; per-handle ops independent.

### Operations
- **Graceful drain:** SIGTERM → `create` returns 503 (draining) + keep existing
  sandboxes until idle or a drain deadline (`terminationGracePeriodSeconds`), then
  exit. Deployment PreStop hook.
- **Orphan idle-reaper:** the app's `InvestigationRegistry.kill_idle` reaps normally;
  the only leak is an **app-pod crash** leaving handles unkilled. Host background sweep
  kills handles idle (incl. no in-flight `exec` output) longer than `idle_ttl`. This is
  a **per-handle** bound — distinct from and **not covered by** the existing
  **per-command** `exec_timeout` / `log_timeout`. Generous default (≈30 min) ≫
  `exec_timeout`; configurable; **logs what it reaps** (no silent cap).
- **Health:** `/healthz` (liveness) + `/readyz` (cgroup v2 present + delegation OK +
  can allocate a uid) so k8s routes `create` only to ready pods; startup fail-loud
  check feeds `/readyz`.

### Config
- **Client** (`SandboxSettings`): `kind: "http"` + `http: {base_url, read_timeout=0}`.
- **Host** (new top-level `sandbox_host:`): `bind`, `uid_min`/`uid_max`,
  `memory_max` ("512M"), `cpu_cores` (1.0), `pids_max`, `cgroup_root` (None=detect,
  injectable for tests), `root`, `exec_timeout`, `log_timeout`, `tools_dir`,
  `idle_ttl`. Friendly units translated to cgroup syntax internally.
- Only `config.example.yaml` is edited; the live `config.yaml` is off-limits — hand
  the operator a snippet.

### Testing (100% gate without root)
- **L1 — client + host wire (unit):** `HttpSandbox` against an **in-process ASGI host**
  (`httpx.ASGITransport`) with **`MockSandbox`** injected. Covers serialization, NDJSON
  streaming parse, raw-bytes, handle encode/decode, error→exception mapping. No root.
- **L2 — `IsolatedProcessSandbox` (unit):** every privileged op is **seamed to run
  non-root by parameterizing its target** — `cgroup_root` injected to a `tmp_path`
  (write real files to a fake tree), `chown` to `os.getuid()` (self), `setfacl` on an
  owned tmp dir, the `setpriv`+cgroup wrapper a **pure argv builder asserted as a
  string**. All lines execute as the dev user ⇒ 100%.
- **L3 — real isolation behaviour (integration):** foreign-uid `setpriv` actually drops
  privilege, `memory.max` actually OOM-kills, cross-uid file/process denial.
  `@pytest.mark.integration` + `skipif(not root / not cgroup v2)`. Validates behaviour;
  **never relied on for coverage** (its lines are covered by L2). Mirrors
  `test_local_process.py` (whole-module `integration`).

---

## Phases (flat integers)

### P1 — Wire protocol + `HttpSandbox` client + host shell
- `sandbox/http_client.py` `HttpSandbox`: 12 methods over HTTP; handle id =
  `encode(pod_url, remote_id)` / decode in every method; NDJSON `exec` streaming →
  `on_output` + `ExecResult`; raw-bytes upload/download; error→exception mapping;
  connection-failure → `SandboxNotFound`; `expose_port` → `NotImplementedError`.
- `sandbox/host/app.py` FastAPI host wrapping an **injected `Sandbox`** (P1 default
  `LocalProcessSandbox(isolate=False)` so it works end-to-end with no isolation yet);
  `create` returns `{pod_url (from POD_IP), remote_id}`.
- **L1 tests** (ASGI + `MockSandbox`): full round-trip, streaming, errors, encode/decode.
- DoD: a usable (un-isolated) HTTP sandbox; `ruff`/`ty` clean; L1 100%.

### P2 — `IsolatedProcessSandbox` (the real isolation)
- `sandbox/isolated_process.py` `IsolatedProcessSandbox(LocalProcessSandbox)`:
  uid/gid **pool allocator** (pure), `create` = chown + `chmod 700` + default ACL +
  per-handle cgroup v2 create, `kill` = free uid + remove cgroup, `_exec_argv` hook =
  `setpriv` + cgroup-join wrapper. Surgical `_exec_argv` extraction in
  `LocalProcessSandbox`.
- cgroup manager (injectable `cgroup_root`), ACL setter, setpriv builder — all seamed.
- **fail-loud** cgroup v2 / delegation check.
- **L2 unit** (non-root, 100%) + **L3 integration** (root-gated, behaviour).
- Host default backend flips to `IsolatedProcessSandbox`.

### P3 — Config + entrypoint wiring
- `SandboxSettings.kind="http"` + `http` sub-config; `get_sandbox` `case "http"`.
- `SandboxHostSettings` + loader keys; `python -m workspace_app.sandbox_host` entrypoint
  (build the injected `IsolatedProcessSandbox` + host app, serve `bind`), boot-step
  narration + config dump.
- `config.example.yaml` additions + operator snippet (live config untouched).

### P4 — Operations
- Graceful drain (SIGTERM → 503 on `create` + drain deadline), orphan idle-reaper
  (`idle_ttl`, logged), `/healthz` + `/readyz` (cgroup/uid readiness fed by the
  startup fail-loud check).

### P5 — Deployment example + docs
- Example k8s manifests (Deployment + HPA + ClusterIP Service + NetworkPolicy + PreStop
  + `terminationGracePeriodSeconds` + downward-API `POD_IP`).
- Fold this plan's locked decisions into a short operator manual; cross-link from
  `sandbox/protocol.py`'s backend list.

---

## Final gate
Full suite + `coverage combine` + `--fail-under=100` (no pipe-mask), `ruff check` +
`ruff format --check`, `ty check`, and a **live canned check**: start a real
`sandbox_host` process locally, point a `HttpSandbox` at it, and exercise
create → upload → exec(stream) → download → walk → kill, asserting isolation
(two handles can't read each other; a `memory.max` breach is killed) under root.
