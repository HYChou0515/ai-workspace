# Production image for the workspace-app (RCA + KB): builds the React SPA, then
# runs the FastAPI backend which serves the API + the built SPA on :8000.
#
# Build (root path "/"):
#   docker build -t rca-app:latest -f docker/Dockerfile .
# Build for a sub-path deploy (e.g. https://company.com/my-svc/rca):
#   docker build -t rca-app:rca --build-arg BASE_PATH=/my-svc/rca/ -f docker/Dockerfile .
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

# ---- stage 2: backend + bundled SPA ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000
# libmagic1: specstar[magic] sniffs uploaded content types during KB ingest.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Dependencies first (cached until pyproject/lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code, then install the project itself.
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# create_app mounts <repo>/web/dist at /; here <repo> == /app.
COPY --from=web /web/dist ./web/dist

EXPOSE 8000
# Honest healthcheck — the API always serves /openapi.json.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"APP_PORT\",\"8000\")}/openapi.json').read()" || exit 1

CMD ["python", "-m", "workspace_app"]
