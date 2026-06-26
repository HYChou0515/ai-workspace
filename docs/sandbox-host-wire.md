# Sandbox host — HTTP wire contract

This is the **contract** between the workspace app's `HttpSandbox` client
(`src/workspace_app/sandbox/http_client.py`) and the standalone **sandbox-host**
service (`sandbox-host/`). The two share **no Python modules** — only this wire
API. The app **defines** it here; the host **implements** it independently
(#251).

Conformance is guarded from both sides:

- **App side** — `tests/sandbox/test_http.py` drives `HttpSandbox` against an
  in-test fake host that mirrors this contract (the app's reference of it).
- **Host side** — `sandbox-host/tests/test_wire.py` drives the real server
  in-process, and `sandbox-host/tests/test_contract.py` (integration) drives it
  over real HTTP through a subprocess.

When you change anything below, update BOTH sides.

## Routing

`POST /sandboxes` hits the host's ClusterIP Service (load-balanced). The reply
carries the chosen pod's own directly-addressable URL (`pod_url`) plus its local
handle id (`remote_id`). The client packs both into the opaque
`SandboxHandle.id` (base64 of `{"u": pod_url, "r": remote_id}`) and connects
**straight to the owning pod** for every later call — so any app replica routes
correctly with no shared state. A dead pod (connection refused) is treated as
`SandboxNotFound`, and the app recreates the sandbox from the FileStore.

## Endpoints

| Method & path | Body / params | Success | Purpose |
|---|---|---|---|
| `POST /sandboxes` | `{image?, env?, exposed_ports?}` | `200 {pod_url, remote_id}` | create |
| `DELETE /sandboxes/{rid}` | — | `204` | kill |
| `POST /sandboxes/{rid}/exec` | `{cmd: [str]}` | `200` NDJSON stream | exec (see below) |
| `PUT /sandboxes/{rid}/file?path=` | raw octet-stream body | `204` | upload |
| `GET /sandboxes/{rid}/file?path=` | — | `200` octet-stream | download |
| `GET /sandboxes/{rid}/exists?path=` | — | `200 {exists: bool}` | exists |
| `GET /sandboxes/{rid}/walk?root=` | — | `200 {entries: [{path,size,version}]}` | walk |
| `DELETE /sandboxes/{rid}/file?path=` | — | `204` | delete |
| `POST /sandboxes/{rid}/mkdir` | `{path}` | `204` | mkdir |
| `DELETE /sandboxes/{rid}/dir?path=` | — | `204` | rmdir |
| `POST /sandboxes/{rid}/rename` | `{src, dst}` | `204` | rename |

Operational (not part of the sandbox surface): `GET /healthz`, `GET /readyz`,
`POST /drain`.

Files cross as **raw `application/octet-stream`** bodies (no base64-in-JSON).
Paths are workspace-root-relative; a leading `/` means the workspace root.

There is **no `expose_port` endpoint** — v1 has no in-sandbox network-service
path. The client's `expose_port` raises `NotImplementedError`. `upload_file` /
`download_to_file` are client-side conveniences over `PUT`/`GET /file`, not
distinct endpoints.

## `exec` — NDJSON streaming

The response is `application/x-ndjson`, one JSON object per line:

- `{"o": "<base64>"}` — a live output chunk (stdout+stderr interleaved), emitted
  as it arrives; the client forwards the decoded bytes to its `on_output` sink.
- Final frame `{"exit": int, "out": "<base64>", "err": "<base64>"}` — the exit
  code plus the **separated** full stdout/stderr buffers, from which the client
  rebuilds `ExecResult`.
- `{"error": "<type>", "detail": "<msg>"}` — if `exec` raised on the host. The
  HTTP status is already `200` (the stream opened), so backend errors travel
  **in-band** as a frame; the client re-raises the mapped exception.
- If the stream ends **before** a final `exit`/`error` frame, the client treats
  it as a dead pod → `SandboxNotFound` (any live `o` chunks already delivered
  are kept).

## Error model

A `404` with body `{"error": "<type>", "detail": "<msg>"}` maps back to the
exception the client raises:

- `SandboxNotFound` — unknown / killed handle, or a dead pod (connection error).
- `FileNotFoundError` — missing file on download / delete / rmdir / rename.

A non-zero command exit is **not** an error — it rides in the exec `exit` frame.

## Auth

None in v1: the host is reachable only inside the cluster namespace
(NetworkPolicy / ClusterIP). Any in-namespace caller can drive it — accepted.
