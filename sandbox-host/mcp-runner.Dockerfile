# One image, every tool (#674).
#
#   docker run --rm -i -v mcp-tools:/cache -v "$PWD:/work" \
#       -e TOOL_ARTIFACT_TOKEN registry/ai-workspace/mcp-runner:<tag> \
#       wafer-history https://gitlab.example/.../tool.manifest.json
#
# WHY ONE IMAGE AND NOT ONE PER TOOL: a bundle already exists in the artifact
# store, so an image per tool stores the same bytes a second time, per tool and
# per version. It also leaves nothing to verify — whatever was copied in is
# what runs. Here the tool arrives by URL and goes through the SAME resolver
# the host uses: the builder gate, the sha, the content-addressed cache. An
# engineer's copy and the platform's are the same bytes, checked the same way,
# and a new publish is picked up on the next start.
#
# The base is the one the builder image anchors to, and for the same reason: a
# bundle carries its own portable interpreter and native wheels, so it runs
# only on the base it was built against. `tests/tooling/test_builder_image.py`
# fails if the two drift apart.
#
# It carries none of the host's runtime (no libreoffice, no node): running a
# tool needs the resolver and nothing else.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# The ABI anchor. The release pipeline sets it to the same value it gives
# tool-builder and the host; out of step is exactly what the gate catches.
ARG BUILDER_ID=tool-builder:dev
# Where the artifact credential may be sent, comma separated. A certificate
# cannot protect this: it is read FROM the manifest, so by the time there is
# anything to verify, the request has been made. Unset, the credential is
# never sent — not knowing where it may go is not a reason to send it anywhere.
ARG ARTIFACT_HOSTS=
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/runner/.venv/bin:$PATH" \
    TOOL_CACHE_DIR=/cache \
    TOOL_BUILDER_ID=${BUILDER_ID} \
    TOOL_ARTIFACT_HOSTS=${ARTIFACT_HOSTS}

WORKDIR /runner
# Dependencies first (cached until the host's pyproject/lock change).
COPY sandbox-host/pyproject.toml sandbox-host/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY sandbox-host/src/ ./src/
RUN uv sync --frozen --no-dev

# Bundles land here, keyed by sha. Mounting a named volume is what makes the
# download a once-per-version cost rather than a once-per-start one; running
# without one works too and simply fetches every start.
#
# Deliberately NOT declared as a VOLUME. That would hand every unmounted run
# an anonymous volume, which `--rm` cleans up and anything else leaves behind
# — one orphan holding a whole unpacked bundle per start, surviving even
# `docker rm`. Left as an ordinary directory, an unmounted cache lives in the
# container's own layer and goes when the container does.
#
# Created here, and writable by any uid, so the image works under
# `--user "$(id -u):$(id -g)"` — which is how a tool's output ends up owned by
# the engineer instead of by root. Without this, `--user` cannot create the
# directory, and a fresh named volume (which inherits this path's mode)
# could not be written either.
#
# STICKY, not merely 0777. On Unix, removing or renaming an entry is governed
# by write permission on the PARENT directory, not by who owns the entry — so
# without `+t` a tool running as `nobody` could rename another tool's
# `/cache/<sha>` away, put its own directory under that name, and be executed
# as that tool on its next start, because `ensure` returns an installed sha
# without reading its bytes again.
#
# Dropping root moved that attack from "overwrite the file" to "replace the
# directory entry"; the sticky bit is what closes it, and is the thing it was
# invented for. It is what `/tmp` has, for this reason.
#
# World-writable at all so the runner works after dropping privileges. One
# case it cannot cover: an engineer who passes `--user` themselves makes the
# unpacking process and the running tool the same uid, and a uid may always
# touch what it created. That is their own account, and the platform — where
# tools genuinely belong to different people — hardens the tree to root
# instead (`tool_cache._harden`).
RUN mkdir -p /cache && chmod 1777 /cache

# Tools resolve relative paths against the working directory, exactly as they
# do on the platform, where it is the user's workspace. Mount the project here.
WORKDIR /work

# The venv's interpreter by absolute path, not a bare `python` off PATH.
# A base image, a CI template or a `docker run -e PATH=...` can all put
# something else first, and the failure is `No module named sandbox_host`
# from an interpreter that was never meant to run this.
ENTRYPOINT ["/runner/.venv/bin/python", "-m", "sandbox_host.mcp_runner"]
