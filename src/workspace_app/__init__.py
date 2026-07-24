"""workspace_app package.

Anything here runs before any submodule — and therefore before any
``import litellm`` — is imported, which is why the litellm cost-map switch below
lives here rather than in a composition root.
"""

import os

# litellm fetches its model-price / context-window map from a GitHub URL over
# HTTPS at IMPORT time (litellm/__init__.py), and the ONLY way to stop it is this
# env var — `litellm.ssl_verify` does not cover it, because the fetch is a raw
# `httpx.get(url)` that bypasses litellm's HTTP client. In a restricted network
# every worker process fails that handshake at startup ("almost every worker: SSL
# getting model settings"), waits out the timeout, and then falls back to the
# bundled backup anyway — so the fetch is pure cost, and the remote map only adds
# a handful of models over the backup, none of them ours. Force the local copy.
#
# `setdefault`, not assignment: an operator who genuinely wants the remote map
# (and can reach it) sets LITELLM_LOCAL_MODEL_COST_MAP=false and we defer. This
# must run before the first `import litellm`, so it belongs at the top of the
# package every entrypoint imports first.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
