"""Run the app with the two sample provisioned tools wired in — for manual testing.

    uv run python scripts/run_with_sample_tools.py

USE THIS, not `python -m workspace_app`: the default entry has no provisioned
tools, so the agent would only see the built-ins and (if a prompt mentions
`data-fetch`) improvise a bogus `exec` call.

It forces `SANDBOX_ISOLATE=false` so the sandbox runs commands on the host with
the workspace dir as cwd — that way `uv` on your PATH is reachable. The default
chroot/userns jail has no `uv`, so a provisioned tool would fail there unless
you bake `uv` + the tool into the sandbox image (the production path).

Then open http://127.0.0.1:8000, start an investigation (the `tool-demo`
template primes the agent), and ask:

    Fetch the "alloy-batches" dataset, then summarise its columns.

The agent calls the `data-fetch` function tool (installed into the sandbox via
`uv sync` on first use) → writes alloy-batches.csv into the workspace → then
`csv-column-summary`. Each tool's deps live in its own sample-tools/ venv.

This file is the template for how a real deployment wires its own tools: define
ToolDefs → pass `tool_defs=` to create_app → list the tool names in an
AgentConfig's `allowed_tools`.
"""

from __future__ import annotations

import os
from pathlib import Path

import msgspec
import uvicorn
from specstar import QB

# Local testing: the LocalProcessSandbox chroot jail has no `uv`; run unjailed so
# the host's `uv` is reachable. (Override by exporting SANDBOX_ISOLATE yourself.)
os.environ.setdefault("SANDBOX_ISOLATE", "false")

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
    isolate = os.environ.get("SANDBOX_ISOLATE")
    url = f"http://{settings.host}:{settings.port}"
    print(
        "\n  Sample tools wired: data-fetch, csv-column-summary"
        f"\n  SANDBOX_ISOLATE={isolate}  (jail off so the sandbox can run uv)"
        f"\n  -> {url}  — new investigation, pick the 'tool-demo' template\n"
    )
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
