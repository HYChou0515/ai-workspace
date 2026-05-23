"""Phase 6 — default sandbox image + kernel-side BE deps.

Per plan-backend §7.5 the bundled image must ship ipykernel +
jupyter_client + a small data-science stack so notebooks can run in
the sandbox out of the box. The backend itself also needs
`jupyter_client` (talks to the kernel from outside) and `ipykernel`
(so the LocalProcessSandbox path works without a separate setup).

These are smoke tests — they guard the contract rather than build
the image. A docker build is too slow + fragile for CI.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.workspace"


def test_workspace_dockerfile_exists():
    assert DOCKERFILE.is_file(), f"missing {DOCKERFILE}"


def test_workspace_dockerfile_ships_required_packages():
    text = DOCKERFILE.read_text()
    for pkg in ("ipykernel", "jupyter_client", "numpy", "pandas", "matplotlib", "scipy"):
        assert pkg in text, f"{pkg} not pinned in Dockerfile.workspace"


def test_workspace_dockerfile_uses_python_3_12_slim():
    text = DOCKERFILE.read_text()
    assert "python:3.12-slim" in text


def test_jupyter_client_importable_in_backend_env():
    """Backend talks to the kernel from outside the sandbox via
    jupyter_client (ZMQ) — Phase 8 KernelService imports this."""
    import jupyter_client  # noqa: F401


def test_ipykernel_importable_in_backend_env():
    """LocalProcessSandbox-based testing spawns the kernel in-process,
    so ipykernel must be available in the BE env too."""
    import ipykernel  # noqa: F401
