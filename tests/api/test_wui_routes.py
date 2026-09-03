"""A WUI's `callTool` — the one way a page reaches outside itself.

The page has no network (its CSP forbids one), so this route is the whole of
its reach, and the two things it must get right are which tools it will run and
whose authority it runs them with.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.wui_routes import register_wui_routes
from workspace_app.resources import AgentConfig
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle
from workspace_app.tooling.external import ExternalTools
from workspace_app.tooling.registry import CommandInfo, PackageInfo

PKG = PackageInfo(
    name="mes",
    install_dir="/tools/mes",
    commands=(
        CommandInfo(name="lot-status", description="Look up a lot.", params_json_schema={}),
    ),
)


class _Sandbox:
    """Records the argv a tool was launched with — the tool contract IS that
    argv, so a double that only returned output would let it drift."""

    def __init__(self, result: ExecResult | None = None):
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.result = result or ExecResult(exit_code=0, stdout=b'{"lot":"A1"}')

    async def exec(self, handle, cmd, on_output=None, env=None):
        self.calls.append(list(cmd))
        self.envs.append(dict(env or {}))
        return self.result


class _Session:
    handle = None


class _Registry:
    """Stands in for the sandbox registry, and records that the WUI asked it —
    going around it would give the item a SECOND sandbox beside its turns'."""

    def __init__(self):
        self.asked: list[str] = []
        self.tools: list[dict[str, str] | None] = []

    async def session(self, item_id: str) -> _Session:
        self.asked.append(item_id)
        return _Session()

    async def ensure_handle(self, session, *, tools=None, on_progress=None) -> SandboxHandle:
        self.tools.append(tools)
        return SandboxHandle(id="h1")


class _Locator:
    def __init__(self, allowed: list[str] | None, env: dict[str, str] | None = None):
        self.allowed = allowed
        self.env = env or {}
        self.asked_verb: list[str] = []

    def require_access(self, slug: str, item_id: str, verb: str) -> str:
        self.asked_verb.append(verb)
        return item_id

    def resolve_agent_config(self, item_id: str) -> AgentConfig:
        return AgentConfig(name="cfg", allowed_tools=self.allowed)

    def env_vars_of(self, item_id: str) -> dict[str, str]:
        return dict(self.env)


def build(
    *,
    allowed: list[str] | None = ["mes"],
    packages: list[PackageInfo] | None = None,
    external: ExternalTools | None = None,
    env: dict[str, str] | None = None,
    sandbox: _Sandbox | None = None,
    registry: _Registry | None = None,
    locator: _Locator | None = None,
):
    app = FastAPI()
    sb = sandbox or _Sandbox()
    reg = registry or _Registry()
    loc = locator or _Locator(allowed, env)

    async def _external(item_id: str) -> ExternalTools:
        return external or ExternalTools()

    register_wui_routes(
        app,
        locator=loc,
        sandbox=sb,
        registry=reg,
        packages=packages if packages is not None else [PKG],
        prebuilt_dir=None,
        resolve_external=_external,
    )
    return TestClient(app), sb, reg, loc


URL = "/a/rca/items/i1/wui/tools/lot-status/call"


def test_runs_an_allowed_package_command_and_returns_its_output_verbatim():
    # Verbatim because the caller is a PROGRAM. Anything we appended to explain
    # ourselves would arrive as part of its JSON.
    client, sandbox, _, _ = build()

    resp = client.post(URL, json={"args": {"lot": "A1"}})

    assert resp.status_code == 200
    assert resp.json() == {"output": '{"lot":"A1"}', "exit_code": 0}
    assert sandbox.calls == [["/tools/mes/launch", "lot-status", '{"lot": "A1"}']]


def test_refuses_a_tool_the_app_does_not_offer_and_says_which():
    # This reaches a person through the page's own error panel, so "which tool,
    # and why not" is the whole of what they can act on.
    client, sandbox, _, _ = build(allowed=[])

    resp = client.post(URL, json={})

    assert resp.status_code == 403
    assert "lot-status" in resp.json()["detail"]
    assert sandbox.calls == []


def test_a_tool_outside_the_allow_list_is_refused_even_though_it_exists():
    # The package is installed and its command is real; the app just did not
    # grant it. Existence is not permission.
    client, sandbox, _, _ = build(allowed=["other-pkg"])

    assert client.post(URL, json={}).status_code == 403
    assert sandbox.calls == []


def test_an_unrestricted_deploy_grants_every_package_command():
    # `allowed=None` means "this deploy did not restrict" for the model's
    # toolset, so it has to mean the same here — a WUI that got MORE than the
    # agent would be the bug, and so would one that got nothing.
    client, sandbox, _, _ = build(allowed=None)

    assert client.post(URL, json={}).status_code == 200
    assert sandbox.calls


def test_reports_why_an_external_tool_could_not_be_resolved():
    # Distinct from "not offered": the app DOES grant it, and the reason is
    # somewhere the reader cannot see (#480).
    client, _, _, _ = build(
        packages=[],
        external=ExternalTools(refused={"lot-status": "artifact store unreachable"}),
    )

    resp = client.post(URL, json={})

    assert resp.status_code == 409
    assert "artifact store unreachable" in resp.json()["detail"]


def test_needs_the_authority_to_change_the_item():
    # A tool can write, so the caller needs the verb they would have needed to
    # make the change by hand.
    client, _, _, locator = build()

    client.post(URL, json={})

    assert locator.asked_verb == ["edit_content"]


def test_hands_the_tool_the_items_environment_so_credentials_stay_off_the_page():
    # The whole reason external calls go through a tool: the page never holds
    # the secret, and cannot choose which secret to ask a person for.
    client, sandbox, _, _ = build(env={"MES_TOKEN": "s3cr3t"})

    client.post(URL, json={})

    assert sandbox.envs[0]["MES_TOKEN"] == "s3cr3t"


def test_shares_the_items_one_sandbox_rather_than_starting_a_second():
    client, _, registry, _ = build()

    client.post(URL, json={})

    assert registry.asked == ["i1"]


def test_mounts_the_bundles_the_item_resolved():
    client, _, registry, _ = build(
        external=ExternalTools(packages=(PKG,), shas={"mes": "abc123"}),
        packages=[],
    )

    client.post(URL, json={})

    assert registry.tools == [{"mes": "abc123"}]


def test_a_failing_tool_is_a_result_not_an_error():
    # The tool's exit-code contract is the tool's, and it means the same here as
    # it does in a turn: a command that ran and failed has an answer.
    client, _, _, _ = build(sandbox=_Sandbox(ExecResult(exit_code=2, stdout=b"no such lot")))

    resp = client.post(URL, json={})

    assert resp.status_code == 200
    assert resp.json() == {"output": "no such lot", "exit_code": 2}


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"ok\xff"])
def test_undecodable_output_still_comes_back(raw: bytes):
    # A tool that emits bytes we cannot decode has still run and still has
    # something to say; losing the whole answer to one bad byte would look
    # exactly like the tool doing nothing.
    client, _, _, _ = build(sandbox=_Sandbox(ExecResult(exit_code=0, stdout=raw)))

    assert client.post(URL, json={}).status_code == 200
