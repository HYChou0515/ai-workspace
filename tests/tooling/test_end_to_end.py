"""§B.T9 end-to-end smoke: prebuild the two sample-tools, discover them,
run a command in a real LocalProcessSandbox, verify output.

This is the integration test that proves the whole §B pipeline holds
together — prebuild + dispatcher contract + registry + sandbox exec +
output formatting. Slow (real uv venv + pip install): marked skip if uv
is missing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _has_uv() -> bool:
    return shutil.which("uv") is not None


@pytest.mark.skipif(not _has_uv(), reason="uv not on PATH")
def test_prebuild_then_discover_data_fetch_yields_expected_schema(tmp_path: Path):
    """Build data-fetch into a prebuilt bundle, then discover_packages
    sees the command + schema with the expected enum + defaults."""
    from workspace_app.tooling.prebuild import build_package
    from workspace_app.tooling.registry import discover_packages

    build_package(
        name="data-fetch", source=_REPO / "sample-tools" / "data-fetch", dst=tmp_path / "data-fetch"
    )
    pkgs = discover_packages(tmp_path)
    by_name = {p.name: p for p in pkgs}
    assert "data-fetch" in by_name
    df = by_name["data-fetch"]
    cmd_names = [c.name for c in df.commands]
    assert cmd_names == ["data-fetch"]
    schema = df.commands[0].params_json_schema
    name_prop = schema["properties"]["name"]
    # The enum constraint round-tripped from pydantic Literal → schema → discovery.
    assert "enum" in name_prop
    assert set(name_prop["enum"]) == {
        "sensor-telemetry",
        "alloy-batches",
        "process-readings",
        "panel-inspection",
    }


@pytest.mark.skipif(not _has_uv(), reason="uv not on PATH")
def test_prebuild_csv_column_summary_yields_two_commands(tmp_path: Path):
    """Multi-command package: both commands appear in commands.json /
    schemas/ — proving the dispatcher's stage-1 enumeration ran during
    prebuild."""
    from workspace_app.tooling.prebuild import build_package
    from workspace_app.tooling.registry import discover_packages

    build_package(
        name="csv-column-summary",
        source=_REPO / "sample-tools" / "csv-column-summary",
        dst=tmp_path / "csv-column-summary",
    )
    pkgs = discover_packages(tmp_path)
    assert pkgs[0].name == "csv-column-summary"
    cmd_names = sorted(c.name for c in pkgs[0].commands)
    assert cmd_names == ["plot", "summarise"]
    # Each schema independently came from its own pydantic Args model.
    for c in pkgs[0].commands:
        assert "csv" in c.params_json_schema["properties"]


@pytest.mark.skipif(not _has_uv(), reason="uv not on PATH")
def test_end_to_end_data_fetch_in_real_sandbox(tmp_path: Path):
    """The deepest smoke: prebuild data-fetch, provision into a real
    LocalProcessSandbox, exec the launcher binary with a JSON args
    string — file appears in the workspace. Exercises every layer in §B.
    """
    from workspace_app.agent.provision import provision_tools
    from workspace_app.sandbox.local_process import LocalProcessSandbox
    from workspace_app.sandbox.protocol import SandboxSpec
    from workspace_app.tooling.prebuild import build_package
    from workspace_app.tooling.registry import discover_packages

    prebuilt = tmp_path / "prebuilt"
    build_package(
        name="data-fetch", source=_REPO / "sample-tools" / "data-fetch", dst=prebuilt / "data-fetch"
    )
    packages = discover_packages(prebuilt)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "sbx", isolate=False)
    handle = await_(sandbox.create(SandboxSpec()))
    try:
        await_(provision_tools(sandbox, handle, packages, prebuilt_dir=prebuilt))
        # Exec the launcher directly (no agent in the loop) — proves the
        # contract works end-to-end at the argv level.
        args = json.dumps({"name": "alloy-batches", "rows": 60})
        result = await_(sandbox.exec(handle, ["../.tools/data-fetch/launch", "data-fetch", args]))
        assert result.exit_code == 0, result.stderr.decode()
        meta = json.loads(result.stdout.decode())
        assert meta["rows"] == 60
        assert meta["columns"] >= 20
    finally:
        await_(sandbox.kill(handle))


@pytest.mark.skipif(not _has_uv(), reason="uv not on PATH")
def test_python_stack_office_libs_importable_in_real_sandbox(tmp_path: Path):
    """#252: prebuild the python-stack venv carrier, provision it into a
    real LocalProcessSandbox, and exec its launcher to import the office
    stack — the real proof that the agent's `python` calls can read/write
    Excel + PowerPoint, not just that the deps are pinned.
    """
    from workspace_app.agent.provision import provision_tools
    from workspace_app.sandbox.local_process import LocalProcessSandbox
    from workspace_app.sandbox.protocol import SandboxSpec
    from workspace_app.tooling.prebuild import build_package
    from workspace_app.tooling.registry import discover_packages

    prebuilt = tmp_path / "prebuilt"
    build_package(
        name="python-stack",
        source=_REPO / "sample-tools" / "python-stack",
        dst=prebuilt / "python-stack",
    )
    packages = discover_packages(prebuilt)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "sbx", isolate=False)
    handle = await_(sandbox.create(SandboxSpec()))
    try:
        await_(provision_tools(sandbox, handle, packages, prebuilt_dir=prebuilt))
        # The carrier's pure-python launcher forwards argv straight to the
        # bundled python, so `launch -c "<src>"` runs it with the venv's
        # site-packages on PYTHONPATH.
        src = "import pptx, openpyxl, xlsxwriter; print('office ok')"
        result = await_(sandbox.exec(handle, ["../.tools/python-stack/launch", "-c", src]))
        assert result.exit_code == 0, result.stderr.decode()
        assert result.stdout.decode().strip() == "office ok"
    finally:
        await_(sandbox.kill(handle))


# tiny sync helper so the test file doesn't need pytest-asyncio just for
# three awaits — most of the §B tests are sync. `asyncio.run` per call
# is fine: each await wraps a single sandbox op.
def await_(awaitable):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(awaitable)
