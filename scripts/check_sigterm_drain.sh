#!/bin/sh
# Live check (plan-graceful-shutdown P2): does SIGTERM run our shutdown while
# an SSE stream is open?
#
# Boots the app, holds one `/api/monitor/stream` open with curl, sends
# SIGTERM, and reports: whether the process exited on its own inside the
# budget, whether `lifespan: shutdown complete` was logged, and whether the
# SSE client got a clean EOF (HTTP 200) rather than a reset. Before the drain
# existed this printed `Waiting for connections to close.` and the process
# never left — the SIGKILL every rollout ended in.
#
# Usage: WORKSPACE_APP_CONFIG=... [WORKSPACE_TOOLS_DIR=...] scripts/check_sigterm_drain.sh [wait_s]
#
# The port is the config's `server.port` — the app has no override, so an
# argument here could only disagree with it (round 1 of #815: the first
# version took one, probed 8342 while the app listened on 8247, and the
# "up after 90s" line reported connection refused as if it were a slow boot).
set -u
WAIT="${1:-30}"
PORT=$(uv run python -c 'from workspace_app.config.loader import load_with_provenance; print(load_with_provenance()[0].server.port)')
TMP="${TMPDIR:-/tmp}/sigterm-drain.$$"
mkdir -p "$TMP"
LOG="$TMP/app.log"
export WORKSPACE_LLM_LOG=0
# The app configures no root logger itself (uvicorn's config covers only its
# own loggers), so the `lifespan:` / `drain:` INFO lines this check reads need
# a handler: run `main()` under a plain basicConfig.
uv run python -c 'import logging; logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"); import workspace_app.__main__ as m; m.main()' > "$LOG" 2>&1 &
APP=$!
i=0
while [ "$i" -lt 90 ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/readyz" 2>/dev/null)
  [ "$code" = "200" ] && break
  i=$((i + 1)); sleep 1
done
if [ "$code" != "200" ]; then
  echo "FAIL: app never answered /api/readyz on port $PORT (last=$code); log tail:"
  tail -20 "$LOG" | cut -c1-140
  kill -9 "$APP" 2>/dev/null
  exit 1
fi
echo "app pid $APP up after ${i}s on port $PORT"
curl -s -N -w '\nCURL_HTTP=%{http_code}\n' "http://127.0.0.1:$PORT/api/monitor/stream" > "$TMP/sse.out" 2>&1 &
CURL=$!
sleep 2
echo "--- SIGTERM at $(date +%T) ---"
kill -TERM "$APP"
sleep "$WAIT"
if kill -0 "$APP" 2>/dev/null; then
  echo "FAIL: app still running ${WAIT}s after SIGTERM (pid $APP) — killing -9"
  kill -9 "$APP"
  rc=1
else
  wait "$APP"; code=$?
  echo "app exited on its own (exit $code; 143 = uvicorn re-raises SIGTERM after a clean shutdown)"
  rc=0
fi
if kill -0 "$CURL" 2>/dev/null; then
  echo "FAIL: the SSE client is still connected"; kill "$CURL"; rc=1
else
  echo "SSE client ended: $(tail -c 60 "$TMP/sse.out" | tr -d '\n')"
fi
echo "--- shutdown lines ---"
grep -a -E "drain: |Shutting down|Waiting for connections|lifespan: shutdown|drained in|did not drain|Application shutdown" "$LOG" | cut -c1-140
grep -aq "lifespan: shutdown complete" "$LOG" || { echo "FAIL: lifespan shutdown never completed"; rc=1; }
exit $rc
