"""The demo deployment's provisioned-tool registry.

These wire the two examples under `sample-tools/` into the app so the
`tool-demo` template (whose `_config.json` lists them in `allowed_tools`) works
out of the box. A real deployment replaces this list with its own `ToolDef`s
and a template that allows them — nothing here is special-cased.

Each tool ships into the sandbox as a PREBUILT, relocatable venv built by
`scripts/prebuild_tools.py`: provisioning just copies the package in and
`invoke` runs its venv binary, so the sandbox needs no uv / network / build
step (see `agent/provision.py`).

Base-python caveat: a relocatable venv still resolves its base python by path,
so build the venv against the python the SANDBOX will have —
  - production (LocalProcessSandbox in a py3.12 pod): the pod's
    `/usr/bin/python` is overlaid into the jail → build against it, isolate on;
  - this dev box has only python 3.9 (the tools need >=3.10) and uv's python
    isn't in the jail → run with `SANDBOX_ISOLATE=false` so the host's python
    is reachable (no chroot).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..agent.provision import ToolDef

_REPO = Path(__file__).resolve().parents[3]
SOURCE_DIR = _REPO / "sample-tools"
# Where prebuild writes the relocatable packages (one folder per tool, each with
# a `.venv`). Override the location with RCA_TOOLS_DIR.
PREBUILT_DIR = Path(os.environ.get("RCA_TOOLS_DIR", _REPO / ".rca-tools"))


def _tool(
    name: str, description: str, positional: list[str], properties: dict[str, Any]
) -> ToolDef:
    return ToolDef(
        name=name,
        description=description,
        prebuilt=str(PREBUILT_DIR / name),
        install_dir=f"tools/{name}",  # workspace-relative: same in jail + unjailed
        invoke=[f"tools/{name}/.venv/bin/{name}"],
        positional=positional,
        params_json_schema={"type": "object", "properties": properties},
    )


DATA_FETCH = _tool(
    "data-fetch",
    "Materialise a named dataset into the workspace as a CSV. The dataset is "
    "chosen by NAME from a fixed catalog (you cannot pass a URL).",
    ["name"],
    {
        "name": {
            "type": "string",
            "enum": ["sensor-telemetry", "alloy-batches", "process-readings", "panel-inspection"],
            "description": "which dataset to materialise",
        },
        "rows": {"type": "integer", "description": "row count (default 25000)"},
    },
)

CSV_SUMMARY = _tool(
    "csv-column-summary",
    "Summarise each column of a CSV in the workspace: dtype, count, nulls, stats.",
    ["csv"],
    {"csv": {"type": "string", "description": "path to the CSV in the workspace"}},
)

SAMPLE_TOOLS = [DATA_FETCH, CSV_SUMMARY]

# tool name -> source repo, for `scripts/prebuild_tools.py`.
SOURCES = {
    "data-fetch": SOURCE_DIR / "data-fetch",
    "csv-column-summary": SOURCE_DIR / "csv-column-summary",
}


def available_sample_tools() -> list[ToolDef]:
    """SAMPLE_TOOLS whose prebuilt package actually exists — so the normal entry
    only advertises a tool the sandbox can really install. Run
    `scripts/prebuild_tools.py` to build them."""
    return [t for t in SAMPLE_TOOLS if t.prebuilt and Path(t.prebuilt).is_dir()]
