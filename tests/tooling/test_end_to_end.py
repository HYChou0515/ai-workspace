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


@pytest.mark.skipif(not _has_uv(), reason="uv not on PATH")
def test_python_stack_importable_via_bare_python_shim_unjailed_350(tmp_path: Path):
    """#350: the agent calls bare `python`, not the carrier's launch path. In an
    UNJAILED sandbox (the model our pods run) the shim must route `python` to the
    provisioned carrier, so `exec(["python", ...])` imports the office stack
    instead of the host's own venv. Same real-carrier setup as the #252 test
    above, but exercises the bare `python` name the bug was actually about."""
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
        src = "import pptx, openpyxl, xlsxwriter; print('office ok')"
        result = await_(sandbox.exec(handle, ["python", "-c", src]))
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


def test_the_starter_we_hand_out_actually_builds(tmp_path: Path):
    """#674: `tool-starter/` is given to an external team as their starting
    point. A template that does not build is worse than none — they cannot
    tell whether they broke it or received it broken, and they have no way to
    ask us that does not cost a day.

    This runs the real author path: prebuild, pack, and the smoke that
    extracts the bundle and exercises the 3-stage contract through its own
    launcher. Slow, like everything else in this file.
    """
    from workspace_app.tooling.builder import BUNDLE_NAME, MANIFEST_NAME, build_artifact

    starter = Path(__file__).resolve().parents[2] / "tool-starter"
    out = tmp_path / "dist"

    manifest = build_artifact(source=starter, out=out, builder_id="test:starter")

    assert manifest.name == "my-tool"
    # Both authoring styles survive a real build: `count` spells the three
    # pieces out, `head` is one decorated function. They reach the manifest
    # identically, which is what makes the choice a matter of taste.
    assert sorted(c.name for c in manifest.commands) == ["count", "head"]
    # The description is what makes a model reach for one; an empty one ships
    # a tool nobody calls, and the template is the example everyone copies.
    assert all(c.description.strip() for c in manifest.commands)
    assert (out / BUNDLE_NAME).is_file()
    assert (out / MANIFEST_NAME).is_file()


def test_a_published_bundle_answers_mcp_over_stdio(tmp_path: Path):
    """#674: the same tool an engineer's own agent can drive.

    Files existing proves nothing — this speaks the protocol to the entry
    point a real build produced, on the interpreter that build shipped.
    """
    import io as _io
    import json as _json
    import subprocess
    import tarfile as _tarfile

    from workspace_app.tooling.builder import BUNDLE_NAME, build_artifact

    starter = Path(__file__).resolve().parents[2] / "tool-starter"
    dist, unpacked = tmp_path / "dist", tmp_path / "unpacked"
    build_artifact(source=starter, out=dist, builder_id="test:mcp")
    with _tarfile.open(fileobj=_io.BytesIO((dist / BUNDLE_NAME).read_bytes())) as tar:
        tar.extractall(unpacked, filter="data")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("one two\nthree\n")

    conversation = "\n".join(
        _json.dumps(m)
        for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "count", "arguments": {"path": "notes.txt"}},
            },
        )
    )
    proc = subprocess.run(
        [str(unpacked / "mcp")],
        input=conversation,
        capture_output=True,
        text=True,
        # cwd is the workspace, exactly as the platform runs a tool — which is
        # what makes the tool's relative path mean the same thing here.
        cwd=workspace,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    replies = [_json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    by_id = {r["id"]: r for r in replies}

    assert "protocolVersion" in by_id[1]["result"]
    assert sorted(t["name"] for t in by_id[2]["result"]["tools"]) == ["count", "head"]
    # And the tool really ran: the answer is the workspace file's real content.
    answered = _json.loads(by_id[3]["result"]["content"][0]["text"])
    assert answered == {"path": "notes.txt", "lines": 2, "words": 3}
