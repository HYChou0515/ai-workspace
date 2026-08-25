#!/usr/bin/env python
"""Live check: closing an environment really closes it, and an out-of-band
delete is survivable.

The unit tests all run against doubles, and every defect this feature has had
lived in the gap between what the app BELIEVED was running and what actually
was. So this drives the real thing over real HTTP: a real `sandbox-host` under
uvicorn (its real `_HostController` and wire routes, including `GET /sandboxes`),
a real app with `sandbox.kind: http` pointing at it, and real requests to the
panel. Only the host's process-isolation backend is stood in for, because it
needs CAP_SETUID and a delegated cgroup root and is not what this exercises.

The scenario is the one that was reported:

  1. warm a sandbox for an item; the host runs it and the panel lists it;
  2. an operator deletes that sandbox out of band (a supported thing to do) —
     the machine is gone, the app's records are not;
  3. press Close: it must answer 204 AND the row must leave the panel. Both
     halves matter. Answering 204 while the row stays is what taught people the
     button was broken, and it stayed for the whole idle window (8 h by default)
     because nothing could tell "already gone" from "could not reach it";
  4. the workspace still works — the next exec builds a NEW sandbox;
  5. a normal Close shuts the machine down, not just the tally.

Usage (starts and stops both services itself):
    uv run python scripts/check_close_environment.py

`--keep` leaves them running for poking at by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_HOST_BOOT = """
import os, sys
sys.path.insert(0, {host_src!r})
import uvicorn
from sandbox_host.app import make_host_app
from sandbox_host.mock import MockSandbox

port = int(os.environ["PORT"])
uvicorn.run(
    make_host_app(
        MockSandbox(cpu_cores=1.0, memory_bytes=512 * 1024**2),
        advertise_url=f"http://127.0.0.1:{{port}}",
        idle_ttl=0.0,  # no reaping: every teardown in this check must be ours
    ),
    host="127.0.0.1",
    port=port,
    log_level="warning",
)
"""

_CONFIG = """server:
  host: 127.0.0.1
  port: {app_port}

sandbox:
  kind: http
  http:
    base_url: http://127.0.0.1:{host_port}

filestore:
  kind: memory
"""


def _tools_dir(repo: Path) -> Path:
    """Where the prebuilt tool packages live — the app refuses to boot without
    them, and a git WORKTREE does not carry them: `.workspace-tools` is build
    output, not a tracked file, so it only exists in the checkout that ran
    `prebuild_tools.py`. Running this script from a worktree therefore has to
    reach across to the main checkout, which `--git-common-dir` names."""
    here = repo / ".workspace-tools"
    if here.is_dir():
        return here
    try:
        common = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--git-common-dir"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - not a checkout
        return here
    main = (repo / common).resolve().parent
    return main / ".workspace-tools"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _req(method: str, url: str, body: dict | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - localhost
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


def _wait(url: str, *, seconds: float = 90.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            _req("GET", url)
            return
        except OSError:
            time.sleep(1.0)
    raise SystemExit(f"timed out waiting for {url}")


class _Check:
    def __init__(self) -> None:
        self.failed = 0

    def __call__(self, label: str, expected: object, actual: object) -> None:
        if expected == actual:
            print(f"  ok   {label} ({actual})")
        else:
            print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
            self.failed += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="leave both services running")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    host_port, app_port = _free_port(), _free_port()
    host_url = f"http://127.0.0.1:{host_port}"
    api = f"http://127.0.0.1:{app_port}/api"

    tmp = Path(tempfile.mkdtemp(prefix="close-check-"))
    (tmp / "boot.py").write_text(_HOST_BOOT.format(host_src=str(repo / "sandbox-host" / "src")))
    (tmp / "config.yaml").write_text(_CONFIG.format(app_port=app_port, host_port=host_port))

    env = dict(os.environ)
    env["WORKSPACE_APP_CONFIG"] = str(tmp / "config.yaml")
    env.setdefault("WORKSPACE_TOOLS_DIR", str(_tools_dir(repo)))

    # Started INSIDE the try, or a service that fails to come up leaves the
    # other one orphaned: `_wait` raises, and a `finally` that has not been
    # entered yet cleans up nothing. That left stray hosts holding ports.
    procs: list[subprocess.Popen] = []
    check = _Check()
    try:
        procs.append(
            subprocess.Popen(  # noqa: S603
                [sys.executable, str(tmp / "boot.py")], env={**env, "PORT": str(host_port)}
            )
        )
        _wait(f"{host_url}/healthz")
        procs.append(subprocess.Popen([sys.executable, "-m", "workspace_app"], env=env))  # noqa: S603
        _wait(f"{api}/apps")

        print("\n=== 0. warm a sandbox for an item")
        _, created = _req("POST", f"{api}/a/rca/items", {"title": "live close check"})
        item = created["resource_id"]
        print(f"  item={item}")
        status, _ = _req("POST", f"{api}/a/rca/items/{item}/exec", {"cmd": ["echo", "hi"]})
        check("exec starts a sandbox", 200, status)

        def running() -> list[dict]:
            _, body = _req("GET", f"{host_url}/sandboxes")
            return [s for s in body["sandboxes"] if s["item_id"] == item]

        def listed() -> list[str]:
            _, body = _req("GET", f"{api}/me/resources")
            return [e["item_id"] for e in body["live"]]

        first = running()
        check("the host is running one for it", 1, len(first))
        check("the panel lists it", [item], listed())

        print("\n=== 1. an operator deletes that sandbox out of band")
        status, _ = _req("DELETE", f"{host_url}/sandboxes/{first[0]['remote_id']}")
        check("the host accepts the delete", 204, status)
        check("the machine is gone", 0, len(running()))
        check("…and the app still believes in it", [item], listed())

        print("\n=== 2. press Close")
        status, _ = _req("DELETE", f"{api}/me/resources/live/{item}")
        check("Close answers 204", 204, status)
        check("the row leaves the panel", [], listed())

        print("\n=== 3. the workspace still works")
        status, _ = _req("POST", f"{api}/a/rca/items/{item}/exec", {"cmd": ["echo", "again"]})
        check("the next exec rebuilds", 200, status)
        second = running()
        check("the host is running one again", 1, len(second))
        check(
            "…and it is a different sandbox",
            True,
            bool(second) and second[0]["remote_id"] != first[0]["remote_id"],
        )

        print("\n=== 4. a normal Close shuts the machine down, not just the tally")
        status, _ = _req("DELETE", f"{api}/me/resources/live/{item}")
        check("Close answers 204", 204, status)
        check("the host is running nothing for it", 0, len(running()))
        check("the panel is empty", [], listed())
    finally:
        if args.keep:
            print(f"\nleft running: host {host_url}, app {api}")
        else:
            for proc in procs:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                    proc.kill()

    print()
    if check.failed:
        raise SystemExit(f"LIVE CHECK FAILED ({check.failed} assertion(s))")
    print("LIVE CHECK PASSED")


if __name__ == "__main__":
    main()
