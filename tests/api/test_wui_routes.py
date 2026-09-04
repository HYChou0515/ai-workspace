"""A WUI's `callTool` — the one way a page reaches outside itself.

The page has no network (its CSP forbids one), so this route is the whole of
its reach, and the two things it must get right are which tools it will run and
whose authority it runs them with.
"""

from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.locator import ItemLocator
from workspace_app.api.wui_routes import register_wui_routes
from workspace_app.resources import AgentConfig
from workspace_app.sandbox.protocol import (
    ExecResult,
    Sandbox,
    SandboxHandle,
    SandboxNotFound,
)
from workspace_app.tooling.external import ExternalTools
from workspace_app.tooling.registry import CommandInfo, PackageInfo

PKG = PackageInfo(
    name="mes",
    install_dir="/tools/mes",
    commands=(CommandInfo(name="lot-status", description="Look up a lot.", params_json_schema={}),),
)


class _Sandbox:
    """Records the argv a tool was launched with — the tool contract IS that
    argv, so a double that only returned output would let it drift."""

    def __init__(self, result: ExecResult | None = None):
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.result = result or ExecResult(exit_code=0, stdout=b'{"lot":"A1"}')

    async def exists(self, handle, path: str) -> bool:
        # Part of the Sandbox protocol. #775 asks it on every
        # ensure_sandbox; no workspace here declares python
        # dependencies, so nothing follows from the answer.
        return False

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
        self.prepared: list[str] = []

    async def session(self, item_id: str) -> _Session:
        self.asked.append(item_id)
        return _Session()

    async def ensure_handle(self, session, *, tools=None, on_progress=None) -> SandboxHandle:
        self.tools.append(tools)
        return SandboxHandle(id="h1")

    async def prepare_project_env(self, session, handle, *, on_output=None) -> None:
        # #775: the WUI shares the item's ONE python environment with its turns,
        # under the item's lock, exactly as it shares their sandbox. Recorded
        # rather than ignored: a double that silently accepts anything cannot
        # show that the route went through the registry instead of around it.
        self.prepared.append(handle.id)


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


#: `allowed=None` is a MEANINGFUL value here — "this deploy did not restrict" —
#: so it cannot double as "the caller said nothing", and the default needs a
#: sentinel of its own.
_UNSET = object()


def build(
    *,
    allowed: list[str] | None | object = _UNSET,
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
    grants = ["mes"] if allowed is _UNSET else cast("list[str] | None", allowed)
    loc = locator or _Locator(grants, env)

    async def _external(item_id: str) -> ExternalTools:
        return external or ExternalTools()

    register_wui_routes(
        app,
        # Contract doubles: each implements the members this route reaches for
        # and nothing else, so a change in what it reaches for shows up as a
        # failure rather than being absorbed by a stub of the whole protocol.
        locator=cast("ItemLocator", loc),
        sandbox=cast("Sandbox", sb),
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
    # #775: and its ONE python environment, prepared under the item's lock —
    # going around that gives a WUI tool call its own `uv sync` racing whatever
    # a turn or a workflow node on the same item is doing, in one directory
    # whose failure path deletes it.
    assert registry.prepared == ["h1"], (
        f"the environment has to come from the registry too: {registry.prepared}"
    )


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


def test_a_name_two_packages_both_export_is_a_conflict_a_person_can_read():
    # A deploy-configuration fault, not this caller's — but an opaque 500 would
    # reach a person through the page's error panel with nothing to act on.
    other = PackageInfo(
        name="legacy",
        install_dir="/tools/legacy",
        commands=(
            CommandInfo(name="lot-status", description="An older one.", params_json_schema={}),
        ),
    )
    client, sandbox, _, _ = build(allowed=["mes", "legacy"], packages=[PKG, other])

    resp = client.post(URL, json={})

    assert resp.status_code == 409
    assert "lot-status" in resp.json()["detail"]
    assert sandbox.calls == []


# ── the build route (rebuild from the pane, with the log in front of you) ──


BUILD_URL = "/a/rca/items/i1/wui/build"


class _BuildSandbox(_Sandbox):
    """Streams output the way a real sandbox does — in pieces, as they arrive —
    so the route is exercised against the shape it has to forward, not a single
    blob at the end."""

    def __init__(self, chunks: list[bytes], exit_code: int = 0):
        super().__init__(ExecResult(exit_code=exit_code, stdout=b"".join(chunks)))
        self.chunks = chunks

    async def exec(self, handle, cmd, on_output=None, env=None):
        self.calls.append(list(cmd))
        self.envs.append(dict(env or {}))
        for chunk in self.chunks:
            if on_output is not None:
                on_output(chunk)
        return self.result


def _events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_build_streams_its_output_while_it_runs():
    # The point of the feature: a rebuild you can WATCH. A route that answered
    # only when the build finished would give a spinner and no idea whether
    # anything is happening — which is what people do not trust.
    sandbox = _BuildSandbox([b"> vite build\n", b"transforming...\n", b"built in 615ms\n"])
    client, _, _, _ = build(sandbox=sandbox)

    resp = client.post(BUILD_URL, json={"folder": "/page"})

    assert resp.status_code == 200
    events = _events(resp)
    assert [e["text"] for e in events if e["type"] == "output"] == [
        "> vite build\n",
        "transforming...\n",
        "built in 615ms\n",
    ]
    assert events[-1] == {"type": "done", "exit_code": 0}


def test_build_runs_pnpm_in_the_pages_own_folder():
    # `package.json`'s `scripts.build` decides WHAT a build is — the standard
    # place. Naming a command in the view file would let a page choose what gets
    # executed on a human's click.
    sandbox = _BuildSandbox([b"ok\n"])
    client, _, _, _ = build(sandbox=sandbox)

    client.post(BUILD_URL, json={"folder": "/page"})

    assert len(sandbox.calls) == 1
    script = " ".join(sandbox.calls[0])
    assert "pnpm run build" in script
    assert "page" in script


def test_the_build_runs_where_the_page_actually_is():
    """`exec` runs with the WORKSPACE ROOT as its working directory, so a
    workspace-absolute path is not one the shell can use.

    Found by opening a real page rather than by reading: every build died with
    `sh: cd: can't cd to /built` before running anything, and the assertion that
    was meant to catch it (`"/page" in script`) pinned the defect instead — the
    string is there either way. Inside the jail it is worse than an error:
    `/built` names the infra area beside the workspace, which exists."""
    sandbox = _BuildSandbox([b"ok\n"])
    client, _, _, _ = build(sandbox=sandbox)

    client.post(BUILD_URL, json={"folder": "/page"})

    script = " ".join(sandbox.calls[0])
    assert "./page" in script
    assert "cd /page" not in script
    assert "cd -- /page" not in script


def test_a_failed_build_is_reported_with_its_own_output():
    # A build that fails is the normal case while someone is iterating. Its
    # compiler errors are the whole value; a status code is not.
    sandbox = _BuildSandbox([b"error: Unexpected token\n"], exit_code=1)
    client, _, _, _ = build(sandbox=sandbox)

    events = _events(client.post(BUILD_URL, json={"folder": "/page"}))

    assert events[-1] == {"type": "done", "exit_code": 1}
    assert "Unexpected token" in events[0]["text"]


def test_build_refuses_a_folder_outside_the_workspace():
    sandbox = _BuildSandbox([b"ok\n"])
    client, _, _, _ = build(sandbox=sandbox)

    for folder in ("/page/../../etc", "page", "/", ""):
        resp = client.post(BUILD_URL, json={"folder": folder})
        assert resp.status_code == 400, folder
    assert sandbox.calls == []


def test_build_needs_the_authority_to_run_things():
    # It runs a command in the item's sandbox. That is the notebook cell's verb,
    # not the file editor's.
    client, _, _, locator = build(sandbox=_BuildSandbox([b"ok\n"]))

    client.post(BUILD_URL, json={"folder": "/page"})

    assert locator.asked_verb == ["execute"]


def test_build_provisions_the_dependencies_the_mirror_never_kept():
    # `node_modules/` is in the mirror's ignore list, deliberately — it is
    # derived, and huge. So a sandbox that has been recycled comes back with
    # `src/`, `package.json` and the lockfile, and no dependencies. A rebuild
    # that assumed them would fail on the first click after a recycle, which is
    # exactly when a person is least equipped to know why: the remedy would be
    # "ask someone to run pnpm install", and needing to know that is the friction
    # a WUI exists to remove.
    sandbox = _BuildSandbox([b"ok\n"])
    client, _, _, _ = build(sandbox=sandbox)

    client.post(BUILD_URL, json={"folder": "/page"})

    script = " ".join(sandbox.calls[0])
    assert "pnpm install" in script
    assert script.index("pnpm install") < script.index("pnpm run build")
    # With a lockfile, the install must be the reproducible one: two installs
    # from one lock that resolve differently make the lock pointless.
    assert "--frozen-lockfile" in script
    assert "pnpm-lock.yaml" in script


def test_a_build_killed_by_the_deadline_says_which_deadline():
    """`sandbox.exec_timeout` (60s by default) is a WALL CLOCK over the whole
    command, and a cold `pnpm install` plus a bundler can outrun it. The backend
    explains itself in `stderr` — which it appends AFTER the output pumps have
    stopped, so it never reaches `on_output` and never reaches the page. What
    the reader saw was the log stopping mid-install and `exit 124`."""
    sandbox = _BuildSandbox([b"lockfile is up to date\n"], exit_code=124)
    client, _, _, _ = build(sandbox=sandbox)

    events = _events(client.post(BUILD_URL, json={"folder": "/page"}))
    said = "".join(e.get("text", "") for e in events)

    assert "124" not in said or "timed out" in said.lower()
    assert "timed out" in said.lower(), said
    # And it names the knobs, because the reader cannot act on a number alone —
    # BOTH of them, since the total deadline and the idle one both come back as
    # 124 and nothing in the result says which fired. Naming only the total sent
    # a reader to the wrong one whenever a build went quiet for a minute, which
    # an install downloading a large package does.
    assert "exec_timeout" in said
    assert "log_timeout" in said
    assert events[-1] == {"type": "done", "exit_code": 124}


def test_a_build_that_blows_up_still_ends_the_stream():
    """The headers are already sent by the time anything can fail, so an
    exception cannot become a 500 — it just ends the response. The page's
    `for await` then falls through with NOTHING said: partial output, no
    verdict, forever. Every path out of here has to post a verdict."""

    class _Exploding(_Sandbox):
        async def exec(self, handle, cmd, on_output=None, env=None):
            if on_output is not None:
                on_output(b"pnpm install\n")
            raise SandboxNotFound("the item was reaped mid-build")

    client, _, _, _ = build(sandbox=_Exploding(ExecResult(exit_code=0)))

    events = _events(client.post(BUILD_URL, json={"folder": "/page"}))

    assert events[-1]["type"] == "done"
    assert events[-1]["exit_code"] != 0
    said = "".join(e.get("text", "") for e in events)
    assert "reaped mid-build" in said, said


def test_output_split_across_chunks_is_not_mojibake():
    """A chunk is a byte fragment, not a string: the backend reads 4096 bytes at
    a time, so a multi-byte character lands astride the boundary. Decoding each
    chunk on its own turned vite's `✓` — which it prints on every build — into
    a replacement character."""
    tick = "✓ 27 modules transformed\n".encode()
    # Split INSIDE the ✓ (three bytes) — splitting between characters proves
    # nothing, which is how this test passed against the broken code once.
    sandbox = _BuildSandbox([tick[:2], tick[2:]])
    client, _, _, _ = build(sandbox=sandbox)

    events = _events(client.post(BUILD_URL, json={"folder": "/page"}))
    said = "".join(e.get("text", "") for e in events)

    assert "✓ 27 modules transformed" in said
    assert "\ufffd" not in said


def test_leaving_the_page_takes_the_build_with_it():
    """A reader who navigates away disconnects. Nothing else stops the command:
    `pnpm install` would keep running against a folder nobody is watching, and
    the next open would start a second one beside it, both writing `dist/`.

    Driven at the ASGI layer because that is where a disconnect exists —
    `TestClient` consumes the whole response and never sends one, so a test
    written through it waits out the build and proves nothing."""

    class _Hanging(_Sandbox):
        def __init__(self):
            super().__init__(ExecResult(exit_code=0))
            self.cancelled = False

        async def exec(self, handle, cmd, on_output=None, env=None):
            self.calls.append(list(cmd))
            if on_output is not None:
                on_output(b"resolving...\n")
            try:
                await asyncio.sleep(30)  # a real install, from this test's view
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return self.result

    sandbox = _Hanging()
    client, _, _, _ = build(sandbox=sandbox)
    app = client.app  # the ASGI app itself, not the client that hides disconnects

    async def drive() -> None:
        body = json.dumps({"folder": "/page"}).encode()
        sent: list[dict] = []
        started = asyncio.Event()

        async def receive():
            if not sent:
                sent.append({})
                return {"type": "http.request", "body": body, "more_body": False}
            # Wait until the first chunk is out, then hang up — a browser
            # closing the tab, not a client that read to the end.
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                started.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/a/rca/items/i1/wui/build",
            "raw_path": b"/a/rca/items/i1/wui/build",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("test", 1),
            "server": ("test", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=10)
        # Asserted HERE, inside the loop. Outside it, `asyncio.run` tears the
        # loop down and cancels every pending task — so the assertion passed
        # with the guard removed, measuring the teardown instead of the fix.
        await asyncio.sleep(0.05)
        assert sandbox.cancelled, "the build outlived the reader who asked for it"

    asyncio.run(drive())
