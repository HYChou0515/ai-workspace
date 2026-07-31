# The second artifact: the same tool, as an MCP server an engineer can run.
#
# Built by CI from the bundle `build-tool` just produced, so it is the same
# bytes the platform runs — not a second build that could drift.
#
# The base is the builder image. That looks odd for a runtime image, and it is
# deliberate: the bundle carries a portable interpreter compiled against a
# specific libc, so it only runs on the base it was built against. Reusing the
# builder image guarantees that with ONE address for the platform team to hand
# out instead of two. It costs the build toolchain in the layer, which is the
# price of not maintaining a second address that can drift out of step.
ARG BUILDER_IMAGE
FROM ${BUILDER_IMAGE}

# The unpacked bundle: the tool, its dependencies, its interpreter, and the
# `mcp` entry point the build injected.
COPY bundle/ /tool/

# Tools resolve paths against the working directory, exactly as they do on the
# platform, where it is the user's workspace. Mount the engineer's project here.
WORKDIR /work

# stdio: the transport every local MCP client speaks.
ENTRYPOINT ["/tool/mcp"]
