# Standalone sandbox-host image (#251).
#
# The host is its OWN service — it shares no Python deps with workspace-app. Its
# runtime image carries only fastapi/uvicorn/pydantic + the isolation tooling
# (setpriv/unshare from util-linux, setfacl from acl). The agent's prebuilt
# tools are baked in as an OPAQUE directory of self-contained bundles, produced
# by the app's toolchain in a throwaway build stage and copied into the runtime
# image — so the host delivers them at /.tools without ever importing
# workspace_app. (Tool DEFINITION stays app-side; tool EXECUTION is host-side;
# the only handoff is this directory.)
#
# Build (from repo root):
#   docker build -t sandbox-host:latest -f sandbox-host/Dockerfile .

# ---- stage 1: prebuild the agent tools with the app's toolchain ----
# This stage carries workspace_app + sample-tools purely to run the prebuild;
# it is discarded — only the resulting self-contained tool bundles are copied
# into the runtime image below.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS tools
WORKDIR /build
ENV UV_LINK_MODE=copy \
    WORKSPACE_TOOLS_DIR=/build/.workspace-tools
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
COPY sample-tools/ ./sample-tools/
COPY scripts/ ./scripts/
RUN uv sync --frozen --no-dev \
    && uv run --no-dev python scripts/prebuild_tools.py

# ---- stage 2: the lean host runtime ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS host
# util-linux: setpriv + unshare (privilege drop / namespaces).
# acl: setfacl (default ACL so root-written files stay writable by the uid).
RUN apt-get update \
    && apt-get install -y --no-install-recommends util-linux acl \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/host/.venv/bin:$PATH" \
    SANDBOX_HOST_BIND=0.0.0.0:8000 \
    SANDBOX_HOST_TOOLS_DIR=/opt/tools
WORKDIR /host

# Dependencies first (cached until the host's pyproject/lock change).
COPY sandbox-host/pyproject.toml sandbox-host/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY sandbox-host/src/ ./src/
RUN uv sync --frozen --no-dev

# The agent's prebuilt tools — an opaque bundle dir the host bind-mounts at
# /.tools inside each sandbox (SANDBOX_HOST_TOOLS_DIR above points here).
COPY --from=tools /build/.workspace-tools /opt/tools

EXPOSE 8000
# The host needs root (setuid/chown to foreign uids) + a delegated cgroup v2
# subtree; it fails loud at boot / on /readyz if cgroup v2 isn't available.
CMD ["python", "-m", "sandbox_host"]
