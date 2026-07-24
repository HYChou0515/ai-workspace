"""Importing the package must stop litellm's import-time remote cost-map fetch.

litellm fetches its model-price / context-window map from a GitHub URL over HTTPS
at IMPORT time (litellm/__init__.py), with no timeout override worth the name and
— crucially — a raw ``httpx.get`` that does NOT honour ``litellm.ssl_verify``. In
a restricted network every worker process hits that handshake at startup and
fails it ("almost every worker: SSL getting model settings"). The remote map only
adds a handful of models over the bundled backup, none of them ours, so the fetch
is pure cost. The only switch that turns it off is the env var below, which must
be set before litellm is first imported.
"""

from __future__ import annotations

import os
import subprocess
import sys

_PRINT = "import workspace_app, os; print(os.environ.get('LITELLM_LOCAL_MODEL_COST_MAP'))"


def _import_in_fresh_process(env: dict[str, str]) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", _PRINT],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return proc.stdout.strip()


def test_importing_the_package_forces_the_local_cost_map():
    """A fresh interpreter that imports workspace_app must come out with the env
    set, so the very next ``import litellm`` reads the bundled map instead of
    reaching for the network."""
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}

    assert _import_in_fresh_process(env) == "true"


def test_an_operator_can_still_opt_back_into_the_remote_map():
    """``setdefault``, not an override — an operator who actually wants the
    remote map (and can reach it) sets the var to anything else and we leave it
    alone."""
    env = {**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "false"}

    assert _import_in_fresh_process(env) == "false"
