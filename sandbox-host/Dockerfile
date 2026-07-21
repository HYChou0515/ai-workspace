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
# Isolation tooling — util-linux: setpriv + unshare (privilege drop /
# namespaces); acl: setfacl (default ACL so root-written files stay writable by
# the uid).
# PLUS the make_deck (#284) toolchain: the host jails processes INSIDE this
# image and ignores SandboxSpec.image, so the deck deps must live here — they
# can't ride the python-only /.tools bundle the way sci-plot does. node runs the
# model's pptxgenjs program; libreoffice-impress + poppler render the .pptx to
# per-slide images for the review pass; fonts-noto-cjk makes CJK render as glyphs
# (what the review model sees == what the user exports), not tofu.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        util-linux acl \
        nodejs npm libreoffice-impress poppler-utils fonts-noto-cjk fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
# pptxgenjs resolvable from a bare `require('pptxgenjs')`. Debian's npm global
# prefix is /usr/local; NODE_PATH (below) lets the jailed `node build.js` find
# it — the host passes os.environ through `setpriv` (no --reset-env), so the env
# survives into the jail (see isolated_process._exec_argv).
RUN npm install -g pptxgenjs@^3.12
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/host/.venv/bin:$PATH" \
    NODE_PATH=/usr/local/lib/node_modules \
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

# Login-shell PATH guard. The hosted sandbox has no userns jail (uid + cgroup
# only, #393), so `python`/`pip` reach the carrier through a per-sandbox
# `.jailbin` dir the exec path prepends to PATH — and `bash -lc` / `sh -lc`
# (which every workflow node command rides) source /etc/profile, which on Debian
# hard-resets PATH and drops it. The jail overlays its own /etc/profile.d; with
# no chroot this is the equivalent, reading the per-sandbox dir back out of
# SANDBOX_JAILBIN. Pinned by tests/sandbox/test_login_shell_path.py.
COPY docker/profile.d/sandbox-jailbin.sh /etc/profile.d/sandbox-jailbin.sh

EXPOSE 8000
# The host needs root (setuid/chown to foreign uids) + a delegated cgroup v2
# subtree; it fails loud at boot / on /readyz if cgroup v2 isn't available.
CMD ["python", "-m", "sandbox_host"]
