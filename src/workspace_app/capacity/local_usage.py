"""`kind: local` with isolation measures its own sandboxes
(docs/plan-capacity-usage.md §6.5).

The sandboxes run in this process's pod, in cgroups this process made, so the
pod-local loop (`api/lifecycle.py`) samples them with the same `UsageSampler`
sandbox-host runs, writing through this process's own recorder. sandbox-host
samples its own. Every other backend has no per-sandbox cgroup to read: no
sampler, and `sandbox_measured` tells the dashboard, which says so.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..sandbox.isolated_process import IsolatedProcessSandbox
from .cgroup_usage import UsageSampler
from .recorder import UsageRecorder

#: How often each sandbox's cgroup is read — sandbox-host's default too.
SAMPLE_S = 60.0


def sandbox_measured(sandbox: Any) -> bool | None:
    """Whether `sandbox`'s sandboxes can be measured one by one: this process
    measures its own isolated ones (while it records — with `capacity.record`
    off nothing is written at all); sandbox-host decides for its own (None —
    not this process's to say); anything else, nothing can."""
    from ..sandbox.http_client import HttpSandbox

    if isinstance(sandbox, IsolatedProcessSandbox):
        return True
    return None if isinstance(sandbox, HttpSandbox) else False


def local_sandbox_sampler(
    sandbox: Any, recorder: UsageRecorder, *, clock: Callable[[], float] = time.time
) -> UsageSampler | None:
    """A sampler over `sandbox`'s own cgroups, or None when it has none."""
    if not isinstance(sandbox, IsolatedProcessSandbox):
        return None
    return UsageSampler(
        sandbox.cgroup_root,
        window_s=recorder.window_s,
        pod=recorder.pod,
        component=recorder.component,
        targets=sandbox.usage_targets,
        write=recorder.write_documents,
        clock=clock,
    )
