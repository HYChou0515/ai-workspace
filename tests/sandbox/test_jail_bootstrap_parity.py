"""The jail's bootstrap script is kept in two places: the API's `kind: local`
sandbox and the sandbox-host service (`kind: http`, production) each carry a
copy, because sandbox-host ships as its own image and imports nothing from the
app. The two must run the same jail -- #847/#848 PR 5 P12 gave each exec its
own `/dev` tmpfs in both -- and nothing but this test says so: round 16's
conformance lens changed the sandbox-host copy and the API's suite stayed green.

The API's copy is the oracle; sandbox-host's is read from its source, since the
root environment does not install that package.

One block differs on purpose and is set aside: how `/.tools` is mounted. The
host mounts each tool an app was granted from an assembled view (#674); the
API binds its one tools dir. Everything else -- the mounts, `/dev`, the links,
the shims -- is the same jail."""

from __future__ import annotations

import ast
from pathlib import Path

from workspace_app.sandbox.local_process import _JAIL_BOOTSTRAP

HOST = Path(__file__).resolve().parents[2] / "sandbox-host/src/sandbox_host/local_process.py"


def _constant(path: Path, name: str) -> str:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError(f"{name} is not assigned at the top of {path}")


def _without_tools_mount(script: str) -> str:
    """The script with its `# Provisioned tools` block (through the first `fi`
    after it) taken out -- and it must have one, or this would compare less
    than it claims."""
    lines = script.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("# Provisioned tools"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip() == "fi")
    assert "/.tools" in "".join(lines[start:end])
    return "".join(lines[:start] + lines[end + 1 :])


def test_sandbox_host_runs_the_same_jail_bootstrap_as_the_api() -> None:
    assert _without_tools_mount(_constant(HOST, "_JAIL_BOOTSTRAP")) == _without_tools_mount(
        _JAIL_BOOTSTRAP
    )
