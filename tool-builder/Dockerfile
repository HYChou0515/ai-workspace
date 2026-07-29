# The image a third-party tool author runs in their CI (#674).
#
#   docker run --rm -v "$PWD:/src" -v "$PWD/dist:/dist" tool-builder build-tool /src /dist
#
# WHY AN IMAGE AND NOT A SCRIPT: a bundle carries its own portable python and
# native wheels, so it only runs on the base it was built against. This image's
# base is the sandbox-host RUNTIME base — the image the agent's tools actually
# execute inside — which makes ABI compatibility structural instead of a
# gamble. `tests/tooling/test_builder_image.py` fails if the two drift apart.
#
# It carries NO workspace-app dependencies: a build needs uv (already in the
# base) and three stdlib-only modules, so an author's CI pulls a small image
# rather than litellm and a data-science stack.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# The ABI anchor recorded into every manifest this image builds. The release
# pipeline sets it to the published image reference; the platform refuses any
# artifact whose `builder` is not the one it expects.
ARG BUILDER_ID=tool-builder:dev
ENV TOOL_BUILDER_ID=${BUILDER_ID}

COPY src/workspace_app/__init__.py /opt/toolchain/workspace_app/__init__.py
COPY src/workspace_app/tooling/ /opt/toolchain/workspace_app/tooling/
COPY tool-builder/bin/ /usr/local/bin/
ENV PYTHONPATH=/opt/toolchain \
    UV_LINK_MODE=copy

WORKDIR /src
