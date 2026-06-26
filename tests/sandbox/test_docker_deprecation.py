"""DockerSandbox is deprecated (#252): production sandboxes now run via the
sandbox-host service (`sandbox.kind: http` / IsolatedProcessSandbox), so
constructing a DockerSandbox should warn the operator.

Unit test — passing an explicit `client` skips the `docker.from_env()`
branch, so this needs neither the `docker` library nor a daemon and runs
in the fast CI subset (unlike the integration suite in test_docker.py).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from workspace_app.sandbox.docker import DockerSandbox

# A non-None client skips the `docker.from_env()` branch entirely, so the
# value never has to be a real DockerClient — `Any` keeps the type checker
# happy without importing the optional `docker` library.
_DUMMY_CLIENT: Any = object()


def test_constructing_docker_sandbox_warns_deprecated():
    with pytest.warns(DeprecationWarning, match="deprecated"):
        DockerSandbox(client=_DUMMY_CLIENT)


def test_deprecation_points_to_sandbox_host():
    """The warning tells the operator where to go instead — the sandbox-host
    service — rather than just 'this is old'."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        DockerSandbox(client=_DUMMY_CLIENT)
    messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("sandbox-host" in m for m in messages), messages
