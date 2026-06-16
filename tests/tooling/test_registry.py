"""§B.T4 registry — read prebuilt bundles + build LLM-facing FunctionTools.

`discover_packages(prebuilt_dir)` walks the prebuilt tree:

    prebuilt_dir/
      datalab/
        commands.json    [{"name": "summarise", ...}, {"name": "plot", ...}]
        schemas/
          summarise.json {"name": ..., "description": ..., "params_json_schema": ...}
          plot.json

and returns one ``PackageInfo`` per top-level subdir.

`build_function_tools(packages, allowed)` expands the `colon` syntax
(`"datalab"` = all / `"datalab:plot"` = single) into one FunctionTool
per selected command, with cross-package name-collision detection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.tooling.registry import (
    CommandInfo,
    PackageInfo,
    build_function_tools,
    discover_packages,
)


def _seed_package(
    prebuilt_dir: Path,
    name: str,
    commands: list[dict],
) -> None:
    """Lay down the prebuilt-on-disk shape `discover_packages` reads."""
    pkg = prebuilt_dir / name
    (pkg / "schemas").mkdir(parents=True)
    summary = [{"name": c["name"], "description": c["description"]} for c in commands]
    (pkg / "commands.json").write_text(json.dumps(summary))
    for c in commands:
        (pkg / "schemas" / f"{c['name']}.json").write_text(json.dumps(c))


def _cmd(cmd_name: str, description: str, **props: dict) -> dict:
    """Build the `<cmd>.json` payload. `cmd_name` (not `name`) so test
    callers can pass a property literally called `name` via **props."""
    return {
        "name": cmd_name,
        "description": description,
        "params_json_schema": {
            "type": "object",
            "properties": props,
            "required": list(props),
        },
    }


def test_discover_packages_loads_each_package_and_its_commands(tmp_path: Path):
    """A multi-package prebuilt dir → one PackageInfo per subdir, each
    carrying its CommandInfo list (sorted by command name for determinism)."""
    pre = tmp_path / "prebuilt"
    _seed_package(
        pre,
        "datalab",
        [
            _cmd("summarise", "Summarise CSV", csv={"type": "string"}),
            _cmd("plot", "Plot CSV", csv={"type": "string"}),
        ],
    )
    _seed_package(
        pre,
        "data-fetch",
        [_cmd("data-fetch", "Fetch a dataset", name={"type": "string"})],
    )

    packages = discover_packages(pre)
    packages_by_name = {p.name: p for p in packages}
    assert set(packages_by_name) == {"datalab", "data-fetch"}

    datalab = packages_by_name["datalab"]
    assert [c.name for c in datalab.commands] == ["plot", "summarise"]
    assert datalab.commands[0].description == "Plot CSV"
    assert datalab.commands[0].params_json_schema["properties"]["csv"]["type"] == "string"


def test_discover_packages_raises_on_subdir_without_commands_json(tmp_path: Path):
    """A half-built package (e.g. uv venv exists, schemas don't) is a
    deploy bug — the old silent-skip caused tools to vanish at runtime
    with no error (see the May-30 RCA): the agent listed only 7 base
    tools instead of 14. Now: raise with the offender's path so the
    operator knows to rerun prebuild_tools.py."""
    pre = tmp_path / "prebuilt"
    pre.mkdir()
    (pre / "halfbuilt").mkdir()
    (pre / "halfbuilt" / "schemas").mkdir()
    # commands.json missing.
    with pytest.raises(RuntimeError, match="halfbuilt.*commands.json"):
        discover_packages(pre)


def test_discover_packages_ignores_stray_files_at_root(tmp_path: Path):
    """A non-dir entry (e.g. someone dropped a `.DS_Store` or a README
    into the prebuilt root) is silently skipped — only sub-dirs are
    candidate packages."""
    pre = tmp_path / "prebuilt"
    pre.mkdir()
    (pre / "stray.txt").write_text("ignore me")
    _seed_package(pre, "good", [_cmd("g", "ok", x={"type": "string"})])
    pkgs = discover_packages(pre)
    assert [p.name for p in pkgs] == ["good"]


def test_discover_packages_raises_on_subdir_without_schemas_dir(tmp_path: Path):
    """commands.json present but `schemas/` missing → raise. The
    second half of the same half-built guard — bundle authoring
    stopped between writing commands.json and writing per-command
    schemas."""
    pre = tmp_path / "prebuilt"
    pre.mkdir()
    (pre / "halfbuilt").mkdir()
    (pre / "halfbuilt" / "commands.json").write_text("[]")  # exists, but no schemas/
    with pytest.raises(RuntimeError, match="halfbuilt.*schemas"):
        discover_packages(pre)


def test_discover_packages_raises_on_command_missing_schema_file(tmp_path: Path):
    """commands.json lists a command but its `schemas/<name>.json` is
    missing → raise. Per-command schema-missing branch — silently
    dropping one command means the LLM sees a different toolset from
    what commands.json advertised, which is exactly the inconsistency
    that fail-fast prevents."""
    pre = tmp_path / "prebuilt"
    (pre / "datalab" / "schemas").mkdir(parents=True)
    (pre / "datalab" / "commands.json").write_text(
        '[{"name":"summarise","description":"s"},{"name":"missing","description":"m"}]'
    )
    (pre / "datalab" / "schemas" / "summarise.json").write_text(
        '{"name":"summarise","description":"s","params_json_schema":{"type":"object","properties":{}}}'
    )
    # missing.json deliberately absent.
    with pytest.raises(RuntimeError, match="datalab.*missing.*schema"):
        discover_packages(pre)


def test_discover_packages_raises_when_prebuilt_dir_missing(tmp_path: Path):
    """No prebuilt dir at all → raise FileNotFoundError. The May-30 RCA
    showed silent-empty led to operators not noticing prebuild had never
    run — a missing PREBUILT_DIR is a setup bug, not a "no packages
    today" signal. Callers that genuinely don't want packages now gate
    on their own registry being empty before calling (see __main__.py)."""
    with pytest.raises(FileNotFoundError, match="prebuild_tools"):
        discover_packages(tmp_path / "does-not-exist")


def test_build_function_tools_expands_pkg_name_to_all_commands(tmp_path: Path):
    """`allowed=["datalab"]` → every command in datalab gets a FunctionTool;
    nothing from other packages."""
    pre = tmp_path / "prebuilt"
    _seed_package(
        pre,
        "datalab",
        [
            _cmd("summarise", "Summarise CSV", csv={"type": "string"}),
            _cmd("plot", "Plot CSV", csv={"type": "string"}),
        ],
    )
    _seed_package(
        pre,
        "data-fetch",
        [_cmd("data-fetch", "Fetch", name={"type": "string"})],
    )
    pkgs = discover_packages(pre)
    tools = build_function_tools(pkgs, allowed=["datalab"])
    assert sorted(t.name for t in tools) == ["plot", "summarise"]


def test_build_function_tools_colon_picks_single_command(tmp_path: Path):
    """`allowed=["datalab:plot"]` → only the plot command, not summarise."""
    pre = tmp_path / "prebuilt"
    _seed_package(
        pre,
        "datalab",
        [
            _cmd("summarise", "Summarise", csv={"type": "string"}),
            _cmd("plot", "Plot", csv={"type": "string"}),
        ],
    )
    pkgs = discover_packages(pre)
    tools = build_function_tools(pkgs, allowed=["datalab:plot"])
    assert [t.name for t in tools] == ["plot"]


def test_build_function_tools_allowed_none_means_include_all_commands(tmp_path: Path):
    """`allowed=None` means \"no restriction\" — include every command
    from every discovered package. Symmetric with `build_tools(None)`
    which exposes every workspace tool. The earlier asymmetric
    behaviour (None ⇒ empty for packages, None ⇒ all for workspace)
    caused the default AgentConfig (`allowed_tools=[]` → `or None`
    in `_agent_for`) to get 9 workspace tools and ZERO package tools,
    so 'what tools do you have?' missed the rca-tools / data-fetch
    suite entirely."""
    pre = tmp_path / "prebuilt"
    _seed_package(
        pre,
        "datalab",
        [_cmd("p", "Plot", x={"type": "string"}), _cmd("s", "Summarise", csv={"type": "string"})],
    )
    _seed_package(pre, "fetch", [_cmd("f", "Fetch", name={"type": "string"})])
    pkgs = discover_packages(pre)
    out = build_function_tools(pkgs, allowed=None)
    names = {t.name for t in out}
    assert names == {"p", "s", "f"}


def test_build_function_tools_allowed_empty_list_returns_empty(tmp_path: Path):
    """An EXPLICIT empty list still means \"nothing\" — distinguishes
    'caller wants no packages' from 'caller didn't restrict'."""
    pre = tmp_path / "prebuilt"
    _seed_package(pre, "datalab", [_cmd("p", "Plot", x={"type": "string"})])
    pkgs = discover_packages(pre)
    assert build_function_tools(pkgs, allowed=[]) == []


def test_build_function_tools_raises_on_cross_package_collision(tmp_path: Path):
    """Two packages exporting the same command name → host startup raises
    with both package names so the deployer knows which to rename."""
    pre = tmp_path / "prebuilt"
    _seed_package(pre, "a", [_cmd("fetch", "A's fetch", x={"type": "string"})])
    _seed_package(pre, "b", [_cmd("fetch", "B's fetch", x={"type": "string"})])
    pkgs = discover_packages(pre)
    with pytest.raises(ValueError, match="fetch.*a.*b|a.*b.*fetch|b.*a.*fetch"):
        build_function_tools(pkgs, allowed=["a", "b"])


def test_build_function_tools_unknown_command_in_known_pkg_is_silently_skipped(
    tmp_path: Path,
):
    """`allowed=["datalab:nope"]` (package exists, command doesn't) →
    empty result, not a crash. Covers the unknown-cmd branch inside the
    colon arm of `_select_commands`."""
    pre = tmp_path / "prebuilt"
    _seed_package(pre, "datalab", [_cmd("summarise", "Summarise", csv={"type": "string"})])
    pkgs = discover_packages(pre)
    assert build_function_tools(pkgs, allowed=["datalab:nope"]) == []


def test_build_function_tools_unknown_pkg_in_allowed_is_silently_skipped(tmp_path: Path):
    """`allowed=["nope"]` → empty (same as legacy `allowed_tools` behaviour:
    unknown names just don't materialise rather than 500)."""
    pre = tmp_path / "prebuilt"
    _seed_package(pre, "datalab", [_cmd("plot", "Plot", x={"type": "string"})])
    pkgs = discover_packages(pre)
    assert build_function_tools(pkgs, allowed=["nope"]) == []


def test_command_info_carries_full_schema(tmp_path: Path):
    """CommandInfo isn't just the metadata — it carries the JSON schema
    that becomes the LLM-facing tool params. Verify the round-trip."""
    pre = tmp_path / "prebuilt"
    _seed_package(
        pre,
        "datalab",
        [
            _cmd(
                "summarise",
                "Summarise",
                csv={"type": "string"},
                plot={"type": "boolean"},
            )
        ],
    )
    pkgs = discover_packages(pre)
    cmd = pkgs[0].commands[0]
    assert isinstance(cmd, CommandInfo)
    assert "csv" in cmd.params_json_schema["properties"]
    assert "plot" in cmd.params_json_schema["properties"]


def test_package_info_install_dir_is_sandbox_relative(tmp_path: Path):
    """The install dir is a sandbox-relative path (`../.tools/<pkg>`),
    not a host path — it's used at exec time to build argv, not at
    schema time."""
    pre = tmp_path / "prebuilt"
    _seed_package(pre, "datalab", [_cmd("p", "Plot", x={"type": "string"})])
    pkgs = discover_packages(pre)
    assert isinstance(pkgs[0], PackageInfo)
    assert pkgs[0].install_dir == "../.tools/datalab"
