"""`python -m sandbox_host` — run the standalone sandbox host service.

Serve glue only (uvicorn, boot fail-loud, drain + reaper wiring); the testable
build logic lives in `service.py` and the operational logic in `app.py`.
Excluded from coverage (serve glue).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal

import uvicorn

from .app import check_cgroup_ready
from .config import load_settings
from .service import build_host_app, resolve_cgroup_root


async def _reaper_loop(controller) -> None:
    interval = max(60.0, min(controller.idle_ttl, 300.0))
    while True:
        await asyncio.sleep(interval)
        reaped = await controller.reap_idle()
        if reaped:
            print(f"reaped idle sandboxes: {reaped}", flush=True)


async def _serve(app, controller, bind_host: str, bind_port: int) -> None:
    loop = asyncio.get_running_loop()
    # SIGTERM (scale-down/rollout) → drain so no new sandboxes land while the
    # pod terminates; a PreStop hook hitting POST /drain is the primary path.
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, controller.start_draining)
    reaper = asyncio.create_task(_reaper_loop(controller)) if controller.idle_ttl > 0 else None
    server = uvicorn.Server(uvicorn.Config(app, host=bind_host, port=bind_port))
    try:
        await server.serve()
    finally:
        if reaper is not None:
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper


def main() -> None:
    settings = load_settings(os.environ)
    bind_host, bind_port = settings.bind.rsplit(":", 1)
    cgroup_root = resolve_cgroup_root(settings)
    print(
        f"→ sandbox host: cgroup_root={cgroup_root} "
        f"uid={settings.uid_min}..{settings.uid_max} bind={settings.bind} "
        f"tools_dir={settings.tools_dir}",
        flush=True,
    )
    check_cgroup_ready(cgroup_root)  # fail loud: isolation needs cgroup v2
    app = build_host_app(settings, pod_ip=os.environ.get("POD_IP"))
    print("✓ sandbox host ready", flush=True)
    asyncio.run(_serve(app, app.state.controller, bind_host, int(bind_port)))


if __name__ == "__main__":
    main()
