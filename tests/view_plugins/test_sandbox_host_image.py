"""The sandbox-host image bakes every view plugin's sandbox half (#847/#848 P9).

Under `sandbox.kind: http` (production) a plugin whose manifest says
`sandbox: {bundle: ...}` means "already in the host's `builtin/`", and the
API's runner answers 502 naming the plugin when it is not there -- so an image
that stopped baking them would break every chart with no test the wiser.

The bake is a shell loop inside `sandbox-host/Dockerfile`. Rather than match
its text, this RUNS that loop, exactly as Docker would hand it to `sh` (line
continuations joined), against this repo's `view-plugins/`, with a `uv` on
PATH that records what it was asked to build. Then it follows the result
through the stages: the tools dir the loop wrote into is the one the host
stage copies to `<SANDBOX_HOST_TOOLS_DIR>/builtin`, the tree the host serves
bundles from.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
from pathlib import Path

import msgspec

from workspace_app.view_plugins.manifest import PluginManifest

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "sandbox-host" / "Dockerfile").read_text()


def _stage(name: str) -> str:
    stages = re.split(r"^FROM\s+\S+\s+AS\s+(\S+)\s*$", DOCKERFILE, flags=re.MULTILINE)
    by_name = dict(zip(stages[1::2], stages[2::2], strict=True))
    return by_name[name]


def _instructions(stage: str) -> list[str]:
    """The stage's instructions as Docker reads them: comments dropped, then
    `\\` + newline continuations joined."""
    lines = [ln for ln in stage.splitlines() if not ln.lstrip().startswith("#")]
    return [i.strip() for i in re.split(r"\n(?=[A-Z]+\s)", "\n".join(lines).replace("\\\n", ""))]


def _env(stage: str, key: str) -> str:
    for ins in _instructions(stage):
        if ins.startswith("ENV "):
            for pair in shlex.split(ins[4:]):
                if pair.startswith(f"{key}="):
                    return pair.split("=", 1)[1]
    raise AssertionError(f"{key} is not set in the stage")


def _plugins_with_a_sandbox_half() -> set[str]:
    out = set()
    for manifest in (REPO / "view-plugins").glob("*/plugin.json"):
        m = msgspec.json.decode(manifest.read_bytes(), type=PluginManifest)
        if m.sandbox is not None and m.sandbox.bundle is not None:
            out.add(m.name)
    return out


def _bake(tmp_path: Path) -> list[list[str]]:
    """Run the tools stage's bake loop; answer each `uv` call's argv."""
    tools = _stage("tools")
    [loop] = [i for i in _instructions(tools) if i.startswith("RUN ") and "view-plugins/" in i]
    log = tmp_path / "uv.log"
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    # one call per line, its args NUL-separated
    fake.write_text(f"#!/bin/sh\nprintf '%s\\0' \"$@\" >> {log}\necho >> {log}\n")
    fake.chmod(0o755)
    work = tmp_path / "build"
    work.mkdir()
    (work / "view-plugins").symlink_to(REPO / "view-plugins")
    env = {
        "PATH": f"{fake.parent}:{os.environ['PATH']}",
        "WORKSPACE_TOOLS_DIR": _env(tools, "WORKSPACE_TOOLS_DIR"),
    }
    subprocess.run(["sh", "-c", loop[4:]], cwd=work, env=env, check=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return [c.split("\0")[:-1] for c in calls]


def test_the_tools_stage_copies_the_plugins_in_before_baking_them():
    ins = _instructions(_stage("tools"))
    copy = ins.index("COPY view-plugins/ ./view-plugins/")
    bake = next(n for n, i in enumerate(ins) if i.startswith("RUN ") and "view-plugins/" in i)
    assert copy < bake


def test_the_bake_builds_every_plugin_sandbox_half_into_the_tools_dir(tmp_path: Path):
    wanted = _plugins_with_a_sandbox_half()
    assert "chart" in wanted  # the manifests were read
    tools_dir = _env(_stage("tools"), "WORKSPACE_TOOLS_DIR")
    built = {}
    for argv in _bake(tmp_path):
        # uv run --no-dev python -c <code> <name> <source> <dst>
        assert argv[:4] == ["run", "--no-dev", "python", "-c"], argv
        code, name, source, dst = argv[4:]
        assert "build_package(" in code and "force=True" in code
        built[name] = (source, dst)
    assert built == {
        name: (f"view-plugins/{name}/sandbox-src", f"{tools_dir}/{name}") for name in wanted
    }


def test_the_host_serves_the_baked_tools_as_its_builtin_tree():
    tools_dir = _env(_stage("tools"), "WORKSPACE_TOOLS_DIR")
    host = _stage("host")
    root = _env(host, "SANDBOX_HOST_TOOLS_DIR")
    # the host's own name for the tree (sandbox_host is a separate project, so
    # read the constant off its module's AST rather than importing it)
    module = ast.parse(
        (REPO / "sandbox-host" / "src" / "sandbox_host" / "tool_cache.py").read_text()
    )
    [builtin] = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["BUILTIN_DIR"]
    ]
    assert f"COPY --from=tools {tools_dir} {root}/{builtin}" in _instructions(host)
