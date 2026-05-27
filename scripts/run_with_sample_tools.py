"""Run the app with the two sample provisioned tools wired in — for manual testing.

    SANDBOX_ISOLATE=false uv run python scripts/run_with_sample_tools.py

(`SANDBOX_ISOLATE=false` so the sandbox runs commands on the host with the
workspace dir as cwd — that way `uv` on your PATH is reachable. The default
chroot/userns jail wouldn't have `uv` unless you bake it into the sandbox.)

Then open http://127.0.0.1:8000, start an investigation, and ask the agent e.g.:

    Fetch the "alloy-batches" dataset, then summarise its columns.

The agent calls `data-fetch` (installed into the sandbox via `uv sync` on first
use) to write alloy-batches.csv into the workspace, then `csv-column-summary` to
summarise it — each tool's deps live in its own sample-tools/ venv, never the app.

This file is the template for how a real deployment wires its own tools: define
ToolDefs → pass `tool_defs=` to create_app → list the tool names in an
AgentConfig's `allowed_tools`.
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import uvicorn
from specstar import QB

from workspace_app.agent.provision import ToolDef
from workspace_app.api import create_app
from workspace_app.factories import (
    Settings,
    get_chunker,
    get_embedder,
    get_filestore,
    get_kb_llm,
    get_runner,
    get_sandbox,
    get_spec,
)
from workspace_app.monitor import SpecstarMonitor
from workspace_app.resources import AgentConfig

_TOOLS = Path(__file__).resolve().parent.parent / "sample-tools"


def _project(name: str) -> str:
    return str(_TOOLS / name)


DATA_FETCH = ToolDef(
    name="data-fetch",
    description=(
        "Materialise a named dataset into the workspace as a CSV. The dataset is "
        "chosen by NAME from a fixed catalog (you cannot pass a URL)."
    ),
    setup=[["uv", "sync", "--project", _project("data-fetch")]],
    invoke=["uv", "run", "--project", _project("data-fetch"), "data-fetch"],
    positional=["name"],
    params_json_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": [
                    "sensor-telemetry",
                    "alloy-batches",
                    "process-readings",
                    "panel-inspection",
                ],
                "description": "which dataset to materialise",
            },
            "rows": {"type": "integer", "description": "row count (default 25000)"},
        },
    },
)

CSV_SUMMARY = ToolDef(
    name="csv-column-summary",
    description="Summarise each column of a CSV in the workspace: dtype, count, nulls, stats.",
    setup=[["uv", "sync", "--project", _project("csv-column-summary")]],
    invoke=["uv", "run", "--project", _project("csv-column-summary"), "csv-column-summary"],
    positional=["csv"],
    params_json_schema={
        "type": "object",
        "properties": {
            "csv": {"type": "string", "description": "path to the CSV in the workspace"}
        },
    },
)

# To use a provisioned tool the AgentConfig must allow it BY NAME — and a
# non-empty allowed_tools also pins the built-ins, so list those too.
_BUILTINS = ["exec", "read_file", "write_file", "edit_file", "ls", "exists", "delete_file"]
_ALLOWED = [*_BUILTINS, "data-fetch", "csv-column-summary"]


def build_app():
    settings = Settings.from_env()
    spec = get_spec(settings)
    app = create_app(
        spec=spec,
        sandbox=get_sandbox(settings),
        filestore=get_filestore(settings, spec),
        runner=get_runner(settings),
        kb_embedder=get_embedder(settings),
        kb_chunker=get_chunker(settings),
        kb_llm=get_kb_llm(settings),
        monitor=SpecstarMonitor(spec),
        tool_defs=[DATA_FETCH, CSV_SUMMARY],
    )
    # create_app seeded the default configs; widen the one the no-config
    # fallback uses (earliest) so every investigation gets the sample tools.
    rm = spec.get_resource_manager(AgentConfig)
    first = next(iter(rm.list_resources(QB.all().sort("created_time").limit(1).build())))
    cfg = first.data
    assert isinstance(cfg, AgentConfig)
    rm.update(
        first.info.resource_id,  # ty: ignore[unresolved-attribute]
        msgspec.structs.replace(cfg, allowed_tools=_ALLOWED),
    )
    return app, settings


def main() -> None:
    app, settings = build_app()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
