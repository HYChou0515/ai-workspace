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
from collections.abc import Callable

import uvicorn

from .app import check_cgroup_ready
from .config import load_settings
from .service import build_host_app, resolve_cgroup_root


async def _reaper_loop(
    controller,
    *,
    tool_cache_max_bytes: int | None = None,
    uv_cache_max_bytes: int | None = None,
) -> None:
    interval = max(60.0, min(controller.idle_ttl, 300.0))
    while True:
        await asyncio.sleep(interval)
        reaped = await controller.reap_idle()
        # #674: a sandbox ending is what frees a third-party bundle, so the
        # cache sweep rides the same tick. Unreferenced bundles are KEPT
        # while there is room — that is what makes a rollback a remount
        # instead of a download — and evicted oldest-first over the ceiling.
        await controller.sweep_tool_cache(max_bytes=tool_cache_max_bytes)
        # #775: and the per-item uv download caches, for the same reason on the
        # same tick — a sandbox ending is when one stops being written to.
        await controller.sweep_uv_cache(max_bytes=uv_cache_max_bytes)
        if reaped:
            print(f"reaped idle sandboxes: {reaped}", flush=True)


def _reaper_task(
    controller,
    *,
    tool_cache_max_bytes: int | None = None,
    uv_cache_max_bytes: int | None = None,
    say: Callable[[str], None] = lambda line: print(line, flush=True),
) -> asyncio.Task | None:
    """The idle reaper, or None when `SANDBOX_HOST_IDLE_TTL` switches it off —
    and, in that case, a word about what goes off WITH it.

    Both cache sweeps ride this task, so `IDLE_TTL=0` means a configured
    ceiling parses, is stored on the settings, and is applied by nobody. That
    was recorded only in a comment in `deploy/sandbox-host.example.yaml`, which
    is not where an operator looks when disk fills up. Same class the app side
    closed for `sandbox.uv_cache_max_bytes`, said at the point where the
    decision is actually made.

    `say` is a seam so the decision can be tested without capturing stdout;
    everything in this module reports through `print`, and so does this.
    """
    if controller.idle_ttl > 0:
        return asyncio.create_task(
            _reaper_loop(
                controller,
                tool_cache_max_bytes=tool_cache_max_bytes,
                uv_cache_max_bytes=uv_cache_max_bytes,
            )
        )
    dead = [
        name
        for name, value in (
            ("SANDBOX_HOST_TOOL_CACHE_MAX_BYTES", tool_cache_max_bytes),
            ("SANDBOX_HOST_UV_CACHE_MAX_BYTES", uv_cache_max_bytes),
        )
        if value is not None
    ]
    if dead:
        say(
            "⚠️ SANDBOX_HOST_IDLE_TTL=0 switches off the idle reaper, and the cache "
            f"sweeps ride it — {', '.join(dead)} will not be applied. Set a non-zero "
            "idle TTL if you want those ceilings enforced."
        )
    return None


async def _serve(
    app,
    controller,
    bind_host: str,
    bind_port: int,
    *,
    tool_cache_max_bytes: int | None = None,
    uv_cache_max_bytes: int | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    # SIGTERM (scale-down/rollout) → drain so no new sandboxes land while the
    # pod terminates; a PreStop hook hitting POST /drain is the primary path.
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, controller.start_draining)
    reaper = _reaper_task(
        controller,
        tool_cache_max_bytes=tool_cache_max_bytes,
        uv_cache_max_bytes=uv_cache_max_bytes,
    )
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
    # Echo the EFFECTIVE timeouts + archive so an operator can confirm the
    # SANDBOX_HOST_* env actually took — the app's config.yaml does NOT reach
    # here (#251/#493), so this print is the only external signal of what the
    # host really runs (a long command is killed at exec_timeout, so a silent
    # default of 60s looked like a mystery hang).
    print(
        f"→ timeouts: exec_timeout={settings.exec_timeout:g}s "
        f"log_timeout={settings.log_timeout:g}s idle_ttl={settings.idle_ttl:g}s "
        f"| nfs_root={settings.nfs_root}",
        flush=True,
    )
    check_cgroup_ready(cgroup_root)  # fail loud: isolation needs cgroup v2
    app = build_host_app(settings, pod_ip=os.environ.get("POD_IP"))
    print("✓ sandbox host ready", flush=True)
    asyncio.run(
        _serve(
            app,
            app.state.controller,
            bind_host,
            int(bind_port),
            tool_cache_max_bytes=settings.tool_cache_max_bytes,
            uv_cache_max_bytes=settings.uv_cache_max_bytes,
        )
    )


if __name__ == "__main__":
    main()
