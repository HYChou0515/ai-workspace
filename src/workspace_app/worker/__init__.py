"""Standalone job worker (#312).

`python -m workspace_app.worker <jobtype>` builds the SAME coordinator bundle the
API builds (via `build_coordinators`), then block-consumes ONE JobType off the
shared queue. Splitting consumers into their own pods lets each JobType scale
under its own k8s HPA while the API Deployment stays small and runs as a pure
producer (`server.run_consumers: false`).

The worker constructs the full bundle (cheap — no connections open until use)
but consumes only its own JobType; the others ride along producer-only so the
index worker can still chain the index→wiki→quality enqueues.

This module holds the pure, unit-tested seams (jobtype → coordinator, and the
consume-until-stopped loop); the settings-driven composition + CLI glue live in
``__main__`` (omitted from coverage like the other entrypoints).
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..coordinators import CoordinatorBundle


# jobtype (CLI token) → CoordinatorBundle attribute. One worker = one JobType =
# one Deployment = one HPA. `card-gen` keeps the hyphen of the user-facing name.
_JOBTYPE_ATTR = {
    "index": "index",
    "wiki": "wiki",
    "card-gen": "card_gen",
    "sanity": "sanity",
    "eval": "eval",
    "graph": "graph",
    # #715: archive imports — a worker pod drains the document writes so a large
    # archive never runs inside an HTTP request.
    "kb-import": "kb_import",
    # #245: the blob-GC reconcile — its live-set rescan materialises every
    # ResourceMeta of every blob-capable model and streams every revision,
    # which OOMed the API pod that ran it.
    "blob-gc": "blob_gc",
    # plan-chat-video-export: the chat-video render — Chromium + ffmpeg, a
    # workload with its own image (`rca-app-chat-video`) and its own memory.
    "chat-video": "chat_video",
}

# JobTypes whose consumer must hold the API's WHOLE model registry, so the
# worker builds them from the API's own composition (`workspace_app.__main__
# .build_app`, never served) instead of `build_bundle`. The blob-GC reconcile
# computes the live blob set from the REGISTERED models only; a consumer with a
# partial registry would quarantine, then delete, every blob the missing models
# reference (#804 P4). `BlobGcCoordinator._check_registry` refuses such a pass
# at run time; this is what makes the check pass by construction.
#
# `chat-video` is here for a different reason: its every file goes through the
# API's `WorkspaceFiles` (quota, jail, mirror), and only `create_app` composes
# that facade — so the worker takes the coordinator the API wired, files and
# all, instead of assembling a second facade that would drift from the first.
API_REGISTRY_JOBTYPES = frozenset({"blob-gc", "chat-video"})


def select_coordinator(bundle: CoordinatorBundle, jobtype: str) -> object:
    """The coordinator this worker should consume. Raises ``ValueError`` for an
    unknown jobtype, or one whose coordinator isn't wired (``sanity`` with no
    LLM factory) — fail loud rather than idle silently on a queue nothing feeds."""
    attr = _JOBTYPE_ATTR.get(jobtype)
    if attr is None:
        raise ValueError(
            f"unknown jobtype {jobtype!r} (use one of: {', '.join(sorted(_JOBTYPE_ATTR))})"
        )
    coordinator = getattr(bundle, attr)
    if coordinator is None:
        raise ValueError(
            f"the {jobtype!r} coordinator is not wired (its LLM seam is unconfigured); "
            "nothing to consume"
        )
    return coordinator


def consume_until_stopped(coordinator: object, stop_event: threading.Event) -> None:
    """Run the coordinator's background consumer until ``stop_event`` is set
    (the worker wires it to SIGTERM/SIGINT), then drain in-flight work and tear
    the consumer down. Pending jobs are durable, so even an abrupt kill is safe —
    they're redelivered to another pod; this just makes shutdown graceful."""
    coordinator.start_consuming()  # ty: ignore[unresolved-attribute]
    try:
        stop_event.wait()
    finally:
        # aclose() polls until the queue drains, then stops the consumer thread.
        asyncio.run(coordinator.aclose())  # ty: ignore[unresolved-attribute]
