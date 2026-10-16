"""The two modules sandbox-host shares with this package are the SAME bytes
(docs/plan-capacity-usage.md §6.5): the `sandbox` rows it writes must be the
rows this package reads, and sandbox-host cannot import this package. Two
implementations kept alike by hand diverge; a byte comparison cannot."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["usage_wire.py", "cgroup_usage.py"])
def test_sandbox_host_carries_the_same_bytes(name: str):
    ours = ROOT / "src" / "workspace_app" / "capacity" / name
    theirs = ROOT / "sandbox-host" / "src" / "sandbox_host" / name

    assert theirs.read_bytes() == ours.read_bytes(), (
        f"sandbox-host's {name} differs: copy {ours.relative_to(ROOT)} over it"
    )
