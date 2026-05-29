"""The demo deployment's tool-package registry.

A real deployment replaces ``PACKAGES`` with its own dict + a template
that lists those packages (or specific commands) in ``allowed_tools``.
Nothing here is special-cased.

Each entry is a host source dir:

    PACKAGES = {
        "data-fetch": <repo>/sample-tools/data-fetch,
        "csv-column-summary": <repo>/sample-tools/csv-column-summary,
    }

The prebuild script (``scripts/prebuild_tools.py``) builds each into a
sandbox-droppable bundle under ``PREBUILT_DIR/<pkg>/``; host startup then
runs ``workspace_app.tooling.registry.discover_packages(PREBUILT_DIR)``
to construct ``PackageInfo`` for each. ``PackageInfo`` carries the
LLM-facing JSON schema (dumped at prebuild time), so adding a tool means
``PACKAGES[<name>] = …`` + re-prebuild — no host registry metadata.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
SOURCE_DIR = _REPO / "sample-tools"
# Where prebuild writes the relocatable packages (one folder per package).
# Override with RCA_TOOLS_DIR. ``discover_packages`` walks this at startup.
PREBUILT_DIR = Path(os.environ.get("RCA_TOOLS_DIR", _REPO / ".rca-tools"))

# {package name → host source dir}. The package name MUST equal the
# ``[project.scripts]`` console_script entry key in the source's
# ``pyproject.toml`` — the launch wrapper invokes ``.venv/bin/<name>``.
PACKAGES: dict[str, Path] = {
    "data-fetch": SOURCE_DIR / "data-fetch",
    "csv-column-summary": SOURCE_DIR / "csv-column-summary",
}
