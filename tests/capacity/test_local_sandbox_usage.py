"""`kind: local` with isolation measures its own sandboxes (docs/plan-capacity-usage.md
§6.5): each sandbox this process created is a usage target, labelled at create,
and the sampler that reads them writes through the process's own recorder."""

from __future__ import annotations

import os

from workspace_app.capacity.cgroup_usage import Target
from workspace_app.capacity.local_usage import local_sandbox_sampler
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.sink import UsageSink
from workspace_app.sandbox.isolated_process import IsolatedProcessSandbox
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec


def _isolated(tmp_path) -> IsolatedProcessSandbox:
    return IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_base=os.getuid(),
        uid_range=1,  # hash % 1 == 0 ⇒ uid == getuid, so chown is a no-op
        acl_runner=lambda argv: None,
        chown_runner=lambda p, u: None,
    )


async def test_a_sandbox_this_process_made_is_a_target_until_it_is_killed(tmp_path):
    sb = _isolated(tmp_path)

    h = await sb.create(SandboxSpec(labels={"user": "alice", "app": "pm"}), sandbox_id="i-1")
    assert sb.usage_targets() == [Target(sandbox="i-1", item="i-1", user="alice", app="pm")]

    await sb.kill(h)
    assert sb.usage_targets() == []


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


async def test_the_sampler_reads_the_sandboxes_cgroups_and_writes_through_the_recorder(tmp_path):
    sb = _isolated(tmp_path)
    await sb.create(SandboxSpec(labels={"user": "alice", "app": "pm"}), sandbox_id="i-1")
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="api-1", window_s=300, queue_max=100)
    clock = {"t": 1_760_501_100.0 + 60}
    sampler = local_sandbox_sampler(sb, recorder, clock=lambda: clock["t"])
    assert sampler is not None
    cg = tmp_path / "cg" / "i-1"
    (cg / "cpu.stat").write_text("usage_usec 0\n")
    (cg / "memory.current").write_text("0\n")
    sampler.sample()
    (cg / "cpu.stat").write_text("usage_usec 4000000\n")
    clock["t"] += 60
    sampler.sample()
    sampler.close()
    recorder.close()

    rows = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "sandbox"]
    assert [(r["user"], r["item"], r["cpu_s"], r["component"]) for r in rows] == [
        ("alice", "i-1", 4.0, "api")
    ]


def test_a_sandbox_without_cgroups_has_no_sampler():
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="p", window_s=300, queue_max=10)
    try:
        assert local_sandbox_sampler(MockSandbox(), recorder) is None
    finally:
        recorder.close()
