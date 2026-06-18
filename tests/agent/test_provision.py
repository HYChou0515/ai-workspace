"""§B.T5 provisioning: install each PackageInfo's prebuilt bundle into
the sandbox.

The new model:

- `PackageInfo` comes from `workspace_app.tooling.registry.discover_packages`
  (it carries `name`, `commands`, sandbox-relative `install_dir`).
- `provision_tools(sandbox, handle, packages, prebuilt_dir=…)` tars
  `prebuilt_dir/<pkg>/` and uploads it to `install_dir`. No setup step;
  the prebuild has already done everything.
- ctx routes via `agent_config.allowed_tools` — colon syntax
  (`"pkg"` for all, `"pkg:cmd"` for one) means a package goes in if ANY
  of its commands appear in the allow list (can't install half a venv).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.provision import ProvisionError, provision_tools
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle, SandboxSpec
from workspace_app.tooling.registry import (
    CommandInfo,
    PackageInfo,
    build_function_tools,
)


def _pkg(name: str, *cmds: str) -> PackageInfo:
    """Build a PackageInfo with one trivial command per name in `cmds`."""
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(
                name=c,
                description=f"{c}",
                params_json_schema={"type": "object", "properties": {}},
            )
            for c in cmds
        ),
        install_dir=f"../.tools/{name}",
    )


class _Recording:
    """Minimal Sandbox stand-in: records exec argv + uploads, returns
    canned results."""

    def __init__(self, results: dict[tuple[str, ...], ExecResult] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.uploads: list[tuple[str, int]] = []
        self._results = results or {}

    async def create(self, spec: SandboxSpec) -> SandboxHandle:  # pragma: no cover
        return SandboxHandle(id="s1")

    async def exec(self, handle, cmd, on_output=None) -> ExecResult:
        self.calls.append(cmd)
        return self._results.get(tuple(cmd), ExecResult(exit_code=0, stdout=b"ok"))

    async def upload(self, handle, data, remote_path) -> None:
        self.uploads.append((remote_path, len(data)))

    async def kill(self, handle) -> None:  # pragma: no cover
        return None


# ─── provision_tools direct ──────────────────────────────────────────


def _seed_prebuilt(prebuilt_dir: Path, name: str) -> None:
    """Lay down a minimal prebuilt tree (just enough for `_tar_tree` to
    archive). No real venv — provision_tools doesn't inspect content."""
    pkg_dir = prebuilt_dir / name
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "launch").write_text("#!/bin/sh\necho hi\n")
    (pkg_dir / "launch").chmod(0o755)
    (pkg_dir / "commands.json").write_text("[]")


async def test_provision_uploads_each_package_archive_into_sandbox(tmp_path: Path):
    """For each PackageInfo, the bundle gets tar'd and uploaded to
    `<install_dir>.provision.tar.gz`, then extracted at install_dir."""
    _seed_prebuilt(tmp_path / "prebuilt", "datalab")
    pkg = _pkg("datalab", "summarise", "plot")
    sb = _Recording()
    await provision_tools(
        sb,  # ty: ignore[invalid-argument-type]
        SandboxHandle(id="s1"),
        [pkg],
        prebuilt_dir=tmp_path / "prebuilt",
    )
    # one upload at the sandbox-relative install path
    assert sb.uploads and sb.uploads[0][0] == "../.tools/datalab.provision.tar.gz"
    assert sb.uploads[0][1] > 0
    # one extract command with the jail-friendly tar flags
    extract = next(c for c in sb.calls if "tar xzf" in " ".join(c))
    cmd = " ".join(extract)
    assert "tar xzf ../.tools/datalab.provision.tar.gz -C ../.tools/datalab --no-same-owner" in cmd
    assert "rm -f ../.tools/datalab.provision.tar.gz" in cmd


async def test_provision_raises_when_extract_fails(tmp_path: Path):
    """Non-zero extract → ProvisionError surfacing the offending package."""
    _seed_prebuilt(tmp_path / "prebuilt", "datalab")
    pkg = _pkg("datalab", "summarise")
    extract_cmd = (
        "sh",
        "-c",
        "mkdir -p ../.tools/datalab && tar xzf ../.tools/datalab.provision.tar.gz "
        "-C ../.tools/datalab --no-same-owner && rm -f ../.tools/datalab.provision.tar.gz",
    )
    sb = _Recording({extract_cmd: ExecResult(exit_code=2, stdout=b"corrupt archive")})
    with pytest.raises(ProvisionError) as exc:
        await provision_tools(
            sb,  # ty: ignore[invalid-argument-type]
            SandboxHandle(id="s1"),
            [pkg],
            prebuilt_dir=tmp_path / "prebuilt",
        )
    assert exc.value.package == "datalab"


async def test_provision_installs_every_package_in_order(tmp_path: Path):
    """Multi-package: each gets its own upload + extract, in order."""
    prebuilt = tmp_path / "prebuilt"
    _seed_prebuilt(prebuilt, "datalab")
    _seed_prebuilt(prebuilt, "fetcher")
    sb = _Recording()
    await provision_tools(
        sb,  # ty: ignore[invalid-argument-type]
        SandboxHandle(id="s1"),
        [_pkg("datalab", "summarise"), _pkg("fetcher", "fetch")],
        prebuilt_dir=prebuilt,
    )
    assert [u[0] for u in sb.uploads] == [
        "../.tools/datalab.provision.tar.gz",
        "../.tools/fetcher.provision.tar.gz",
    ]


# ─── ctx.ensure_sandbox provisions allowed packages ───────────────────


async def test_ensure_sandbox_provisions_packages_matching_allowed_tools(tmp_path: Path):
    """A package whose name appears in allowed_tools (bare or as a colon
    prefix) gets provisioned; one that doesn't is skipped."""
    prebuilt = tmp_path / "prebuilt"
    _seed_prebuilt(prebuilt, "datalab")
    _seed_prebuilt(prebuilt, "irrelevant")
    sb = _Recording()
    ctx = AgentToolContext(
        sandbox=sb,  # ty: ignore[invalid-argument-type]
        agent_config=AgentConfig(name="a", allowed_tools=["datalab"]),
        packages=[_pkg("datalab", "summarise"), _pkg("irrelevant", "x")],
        prebuilt_dir=prebuilt,
    )
    await ctx.ensure_sandbox()
    uploaded = {u[0] for u in sb.uploads}
    assert "../.tools/datalab.provision.tar.gz" in uploaded
    assert "../.tools/irrelevant.provision.tar.gz" not in uploaded


async def test_ensure_sandbox_provisions_package_when_only_one_command_allowed(
    tmp_path: Path,
):
    """`allowed_tools=["datalab:plot"]` still installs the whole datalab
    package — you can't install half a venv. Only the LLM's tool-list
    filtering scopes it down to just `plot`."""
    prebuilt = tmp_path / "prebuilt"
    _seed_prebuilt(prebuilt, "datalab")
    sb = _Recording()
    ctx = AgentToolContext(
        sandbox=sb,  # ty: ignore[invalid-argument-type]
        agent_config=AgentConfig(name="a", allowed_tools=["datalab:plot"]),
        packages=[_pkg("datalab", "summarise", "plot")],
        prebuilt_dir=prebuilt,
    )
    await ctx.ensure_sandbox()
    assert any(u[0] == "../.tools/datalab.provision.tar.gz" for u in sb.uploads)


async def test_ensure_sandbox_skips_provision_when_no_packages_match(tmp_path: Path):
    """Nothing in allowed_tools matches any package → no upload, no exec."""
    prebuilt = tmp_path / "prebuilt"
    _seed_prebuilt(prebuilt, "datalab")
    sb = _Recording()
    ctx = AgentToolContext(
        sandbox=sb,  # ty: ignore[invalid-argument-type]
        agent_config=AgentConfig(name="a", allowed_tools=["nothing-here"]),
        packages=[_pkg("datalab", "summarise")],
        prebuilt_dir=prebuilt,
    )
    await ctx.ensure_sandbox()
    assert sb.uploads == []


async def test_ensure_sandbox_skips_provision_when_prebuilt_dir_none() -> None:
    """When `prebuilt_dir is None` on the ctx, no provisioning happens
    even if packages + allowed_tools match — the deployer has signalled
    the sandbox already exposes tools by another mechanism (e.g.
    LocalProcessSandbox's `tools_dir` bind-mounts PREBUILT_DIR
    read-only at `/.tools`).

    Regression: production __main__.py used to pass both `tools_dir`
    (bind-mount) AND `prebuilt_dir` (tar-extract via provision_tools)
    pointing at the same PREBUILT_DIR. Inside the jail the bind-mount
    is read-only and overlays the .tar.gz that upload had landed on
    host-side `<sandbox-root>/.tools/` — `tar xzf ../.tools/<pkg>.tar.gz`
    then couldn't find the archive (shadowed by the mount) and exited
    2. Symptom: first package tool call (`summarise`, `wafer-history`)
    failed with `provisioning '<pkg>' failed at \\`tar -C ../.tools/<pkg>\\`
    (exit 2)`."""
    sb = _Recording()
    ctx = AgentToolContext(
        sandbox=sb,  # ty: ignore[invalid-argument-type]
        agent_config=AgentConfig(name="a", allowed_tools=["datalab"]),
        packages=[_pkg("datalab", "summarise")],
        prebuilt_dir=None,
    )
    await ctx.ensure_sandbox()
    assert sb.uploads == []
    # And no `tar` / `mkdir -p ../.tools/...` extract command either.
    assert not any("tar" in " ".join(c) for c in sb.calls)


# ─── runner _agent_for hooks the package commands as FunctionTools ────


def test_agent_for_exposes_allowed_package_commands_as_function_tools():
    """`allowed_tools=["datalab"]` puts every datalab command in the
    agent's tool list (plus the built-ins from build_tools)."""
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(
        AgentConfig(name="a", allowed_tools=["exec", "datalab"]),
        packages=[_pkg("datalab", "summarise", "plot")],
    )
    names = {t.name for t in agent.tools}
    assert "summarise" in names
    assert "plot" in names
    assert "exec" in names  # built-in still there
    assert "data-fetch" not in names  # not in any allowed entry


def test_agent_for_exposes_all_package_commands_when_allowed_tools_empty():
    """A bare AgentConfig (no `allowed_tools` set, defaults to `[]`)
    exposes EVERY package command — same semantics as for workspace
    tools (empty list → no restriction). The asymmetric prior
    behaviour (workspace=all, packages=none) caused the default
    deploy config to ship 9 workspace tools and ZERO package tools;
    the LLM's 'what tools do you have?' answer missed the entire
    rca-tools / data-fetch / csv-column-summary suite."""
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(
        AgentConfig(name="a"),  # allowed_tools defaults to []
        packages=[_pkg("datalab", "summarise", "plot"), _pkg("fetch", "f")],
    )
    names = {t.name for t in agent.tools}
    # Every package command is exposed.
    assert "summarise" in names
    assert "plot" in names
    assert "f" in names
    # Workspace tools still all there.
    assert "exec" in names


def test_agent_for_colon_filters_to_one_command():
    """`allowed_tools=["datalab:plot"]` → only `plot` exposed, not
    summarise — the LLM doesn't see the whole package."""
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(
        AgentConfig(name="a", allowed_tools=["datalab:plot"]),
        packages=[_pkg("datalab", "summarise", "plot")],
    )
    names = {t.name for t in agent.tools}
    assert "plot" in names
    assert "summarise" not in names


def test_agent_for_threads_base_url_and_api_key_to_the_model():
    from agents.extensions.models.litellm_model import LitellmModel

    from workspace_app.agent.repairing_model import RepairingModel
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(AgentConfig(name="a"), base_url="https://hosted/v1", api_key="sk-1")
    # #76: the model is wrapped in RepairingModel for tool-arg self-repair; the
    # creds still thread through to the inner LitellmModel (transparent passthrough).
    assert isinstance(agent.model, RepairingModel)
    assert isinstance(agent.model._inner, LitellmModel)
    assert agent.model.base_url == "https://hosted/v1"
    assert agent.model.api_key == "sk-1"


def test_agent_for_threads_reasoning_effort_to_model_settings():
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(AgentConfig(name="a"), reasoning_effort="high")
    assert agent.model_settings.reasoning is not None
    assert agent.model_settings.reasoning.effort == "high"
    # absent → no reasoning settings (model's default; nothing forced)
    assert _agent_for(AgentConfig(name="a")).model_settings.reasoning is None


def test_agent_for_reasoning_off_ollama_sets_think_false_extra_args():
    """ "none" on an Ollama model → ModelSettings.extra_args think=False (the SDK
    splats extra_args as a top-level completion kwarg → litellm ollama think)."""
    from workspace_app.api.litellm_runner import _agent_for

    # default AgentConfig model is ollama_chat/qwen3:14b.
    ms = _agent_for(AgentConfig(name="a"), reasoning_effort="none").model_settings
    assert ms.reasoning is None  # reasoning OFF, not an effort level
    assert ms.extra_args == {"think": False}
    assert ms.extra_body is None


def test_agent_for_reasoning_off_vllm_sets_enable_thinking_false_extra_body():
    """ "none" on a non-Ollama (vLLM/openai-compatible) model → ModelSettings
    extra_body chat_template_kwargs enable_thinking=False."""
    from workspace_app.api.litellm_runner import _agent_for

    ms = _agent_for(
        AgentConfig(name="a", model="hosted_vllm/qwen3-32b"), reasoning_effort="none"
    ).model_settings
    assert ms.reasoning is None
    assert ms.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert ms.extra_args is None


# ─── function-tool on_invoke execs launch <cmd> '<args-json>' ─────────


async def test_function_tool_passes_args_json_through_to_sandbox_exec():
    """The LLM-given args_json reaches the sandbox unchanged as argv[2] of
    the launch command — no argparse translation."""
    sb = _Recording()
    actx = AgentToolContext(sandbox=sb, handle=SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]
    pkg = _pkg("datalab", "summarise")
    [tool] = build_function_tools([pkg], allowed=["datalab:summarise"])
    args = json.dumps({"csv": "x.csv"})
    out = await tool.on_invoke_tool(RunContextWrapper(actx), args)  # ty: ignore[invalid-argument-type]
    assert sb.calls[-1] == ["../.tools/datalab/launch", "summarise", args]
    assert "ok" in out


async def test_function_tool_substitutes_empty_args_json_with_curly_braces():
    """LLM occasionally calls a tool with empty args; the dispatcher
    contract requires SOME JSON. We pass `{}` so it gets validated as an
    empty object rather than `pydantic` choking on an empty string."""
    sb = _Recording()
    actx = AgentToolContext(sandbox=sb, handle=SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]
    pkg = _pkg("datalab", "summarise")
    [tool] = build_function_tools([pkg], allowed=["datalab"])
    await tool.on_invoke_tool(RunContextWrapper(actx), "")  # ty: ignore[invalid-argument-type]
    assert sb.calls[-1] == ["../.tools/datalab/launch", "summarise", "{}"]
