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
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/runner/.venv/bin:$PATH" \
    TOOL_CACHE_DIR=/cache \
    TOOL_BUILDER_ID=${BUILDER_ID}

WORKDIR /runner
# Dependencies first (cached until the host's pyproject/lock change).
COPY sandbox-host/pyproject.toml sandbox-host/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY sandbox-host/src/ ./src/
RUN uv sync --frozen --no-dev

# Bundles land here, keyed by sha. Mount a volume: that is what makes the
# download a once-per-version cost rather than a once-per-start one.
VOLUME /cache

# Tools resolve relative paths against the working directory, exactly as they
# do on the platform, where it is the user's workspace. Mount the project here.
WORKDIR /work

ENTRYPOINT ["python", "-m", "sandbox_host.mcp_runner"]
