# Production image for the workspace-app (RCA + KB): builds the React SPA, then
# runs the FastAPI backend which serves the API + the built SPA on :8000.
#
# Build (root path "/"):
#   docker build -t rca-app:latest -f docker/Dockerfile .
# Build for a sub-path deploy (e.g. https://company.com/my-svc/rca):
#   docker build -t rca-app:rca --build-arg BASE_PATH=/my-svc/rca/ -f docker/Dockerfile .
# Build the chat-video WORKER image (the app + Chromium + ffmpeg, +1.63 GB):
#   docker build --target chat-video -t rca-app-chat-video:latest -f docker/Dockerfile .
#
# Docker builds the LAST stage when no --target is given, so the last stage
# here is `api` — the app image as is. The worker stage sits before it and is
# reached only by name; while it was the last stage (#823), a bare build
# handed every API pod the worker image.
#
# (This is the APP image. docker/Dockerfile.workspace is the agent SANDBOX image.)

# ---- stage 1: build the SPA ----
FROM node:22-bookworm-slim AS web
WORKDIR /web
RUN npm install -g pnpm@9
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# BASE_PATH is baked into the bundle (Vite `base` + router basename + fetch
# prefix all derive from it). Must match the ingress path. Default "/".
ARG BASE_PATH=/
RUN VITE_BASE_PATH="$BASE_PATH" pnpm run build   # → /web/dist

# ---- stage 1b: the runtime view plugins (#847/#848) ----
# Each `view-plugins/<name>/` is built by the ONE shared script into
# /out/<name>/{plugin.json, web/index.js, skill/, scenarios/} and copied into
# the app below at its default plugin dir. `csv-table` is one of them — it left
# the SPA bundle, so an image built around this stage loses that view kind.
# Web halves only: under `sandbox.kind: http` (production) a plugin's sandbox
# half lives in sandbox-host, not here. Running THIS image with
# `sandbox.kind: local`, a plugin with a `bundle` sandbox half refuses boot (no
# `sandbox/`) — mount a plugin dir installed by `view_plugin build` instead.
FROM node:22-bookworm-slim AS view-plugins
WORKDIR /src
RUN npm install -g pnpm@9
COPY view-plugins/ ./view-plugins/
RUN mkdir -p /out \
    && for d in view-plugins/*/; do \
         if [ -f "$d/plugin.json" ]; then node view-plugins/build-web.mjs "$d" /out || exit 1; fi; \
       done

# ---- stage 2: backend + bundled SPA ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000
# libmagic1: specstar[magic] sniffs uploaded content types during KB ingest.
# libreoffice-impress: headless `soffice --convert-to pdf` — the KB
#   PptxParser's slide→PDF step (issue #39). Impress alone (not the full
#   suite) keeps the layer as small as LibreOffice gets.
# fonts-noto-cjk + fonts-liberation: the slim base ships no fonts; without
#   them converted slides render CJK/Latin text as tofu — which also
#   poisons what the VLM sees.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmagic1 \
        libreoffice-impress \
        fonts-noto-cjk \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Dependencies first (cached until pyproject/lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code, then install the project itself.
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Built-in shared skills + workflow templates. Their registries resolve the
# source dir as `Path(__file__).parents[3] / "sample-skills"` — and with the
# editable install above, `__file__` is `/app/src/workspace_app/...`, so
# parents[3] is `/app`. These must therefore land at `/app/sample-skills` and
# `/app/sample-workflows` or every built-in skill/template silently vanishes from
# the picker (profile skills, which ship inside the package, are unaffected).
# `sample-tools/` is NOT copied on purpose: a tool bundle is mounted, not read
# from here. Keep in sync with apps/shared_skills.py + workflow/shared.py.
COPY sample-skills/ ./sample-skills/
COPY sample-workflows/ ./sample-workflows/

# create_app mounts <repo>/web/dist at /; here <repo> == /app.
COPY --from=web /web/dist ./web/dist
# #847/#848: the view plugins, at the default `view_plugins.dir`
# (<repo>/.view-plugins). An operator mounting their own dir instead must carry
# `csv-table` into it too (docs/migrations.md).
COPY --from=view-plugins /out ./.view-plugins
# ...and refuse an image whose plugins would break in the browser (a bundled
# React, a dev build, a leftover `process.env`) or refuse to boot.
RUN python -m workspace_app.view_plugin check

# Login-shell PATH guard, for `sandbox.kind: local` — the app then runs the
# agent's `exec` in THIS image, unjailed, and `bash -lc "python3 …"` loses the
# `.jailbin` shim dir to /etc/profile's PATH reset. Same file the sandbox-host
# image installs. Note this image ships NO prebuilt tools, so the shims here
# resolve to the /usr/bin/python3 fallback rather than a carrier — the guard is
# what makes an operator-mounted `sandbox.tools_dir` work at all, not something
# this image exercises on its own.
# Pinned by tests/sandbox/test_login_shell_path.py.
COPY docker/profile.d/sandbox-jailbin.sh /etc/profile.d/sandbox-jailbin.sh

EXPOSE 8000
# Honest healthcheck — the API always serves /openapi.json.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"APP_PORT\",\"8000\")}/openapi.json').read()" || exit 1

CMD ["python", "-m", "workspace_app"]

# ---- stage 3: the chat-video worker (plan-chat-video-export) ----
# The same app, plus a headless Chromium and ffmpeg — the two tools a chat
# video needs and nothing else in the platform does. Its own image so the
# API pod (and every other worker) does not carry the 1.63 GB they add;
# `kubernetes/base/workers.yaml` runs it as `rca-worker-chat-video`.
#
#   docker build --target chat-video -t rca-app-chat-video:latest -f docker/Dockerfile .
#
# NOT the last stage: see `api` below.
#
# `--with-deps` installs Chromium's shared libraries through apt; the
# browser itself lands under PLAYWRIGHT_BROWSERS_PATH, outside the venv, so
# a later `uv sync` cannot remove it. A build with no reach to Playwright's
# CDN uses apt's chromium instead — `chat_video.chromium_path` + a symlink
# for the recorder's ffmpeg; the recipe is in docs/deployment.md §11 (斷網). The worker boots the API's own
# composition (`build_app`, never served — see workers.yaml), so it needs
# what the API needs at boot: the same configmap and mounts.
FROM app AS chat-video
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv sync --frozen --no-dev --extra chat-video \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
CMD ["python", "-m", "workspace_app.worker", "chat-video"]

# ---- stage 4: the API image, the default target ----
# Nothing but a name: a `docker build` with no --target builds the last stage,
# and the app image is what that must be (tests/chat_video/test_image.py).
FROM app AS api
