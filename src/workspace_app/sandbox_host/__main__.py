"""`python -m workspace_app.sandbox_host` — run the sandbox host service.

Serve glue only (uvicorn + boot narration); the testable build logic is in
`service.py`. Excluded from coverage like the app's top-level `__main__`.
"""

from __future__ import annotations

import os

import uvicorn

from ..config.loader import load
from .service import build_host_app


def main() -> None:
    settings = load()
    host = settings.sandbox_host
    bind_host, bind_port = host.bind.rsplit(":", 1)
    pod_ip = os.environ.get("POD_IP")
    print(
        f"→ sandbox host: cgroup_root={host.cgroup_root or '/sys/fs/cgroup'} "
        f"uid={host.uid_min}..{host.uid_max} bind={host.bind}",
        flush=True,
    )
    app = build_host_app(settings, pod_ip=pod_ip)
    print("✓ sandbox host ready", flush=True)
    uvicorn.run(app, host=bind_host, port=int(bind_port))


if __name__ == "__main__":
    main()
