"""P8 — an app's third-party tools reach the turn that will use them (#674).

`TurnContextBuilder` takes ~20 collaborators, and the behaviour under test
needs two of them, so the method is exercised directly rather than through a
whole app. What matters is the wiring: the app's declaration is read from its
manifest, resolved against the backend, and the answer arrives whole.
"""

from __future__ import annotations

from typing import Any, cast

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.locator import ItemLocator
from workspace_app.api.registry import InvestigationRegistry
from workspace_app.api.turn_context import TurnContextBuilder
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import Sandbox, SandboxHandle, SandboxSpec
from workspace_app.tooling.external import MountedTool


class _Session:
    """A registry session with no sandbox yet — the cold case, where this
    turn's create is what mounts the bundles."""

    handle: SandboxHandle | None = None
    tools: dict[str, MountedTool] | None = None


class _Locator:
    def __init__(self, slug: str | None) -> None:
        self._slug = slug

    def slug_of(self, _item_id: str) -> str | None:
        return self._slug


class _Host:
    resolves_tools = True

    def __init__(self, *, stale: bool = False) -> None:
        self.asked: list[dict[str, str]] = []
        self.stale = stale

    async def resolve_tools(self, declared: dict[str, str]) -> dict[str, Any]:
        self.asked.append(declared)
        return {
            "tools": {
                name: {
                    "sha": "a" * 64,
                    "version": "1.4.2",
                    "author": "Wafer Team <wafer@example.com>",
                    "stale": self.stale,
                    "commands": [{"name": "trend", "description": "d", "params_json_schema": {}}],
                }
                for name in declared
            },
            "refused": {},
        }


class _Registry:
    """What `_external_tools` asks of the registry: what the item's live
    sandbox was created with (this pod's or a peer's — the registry decides,
    probes and bounds it; see `test_registry.py`)."""

    def __init__(self, mounted: dict[str, MountedTool] | None = None) -> None:
        self.mounted = mounted
        self.asked: list[str] = []

    async def mounted_tools(self, item: str) -> dict[str, MountedTool] | None:
        self.asked.append(item)
        return self.mounted


def _builder(
    *,
    slug: str | None,
    sandbox: object,
    plugins: dict[str, str] | None = None,
    registry: _Registry | None = None,
) -> TurnContextBuilder:
    builder = object.__new__(TurnContextBuilder)
    builder._locator = _Locator(slug)  # type: ignore[attr-defined]
    builder._sandbox = sandbox  # type: ignore[attr-defined]
    builder._view_plugin_artifacts = dict(plugins or {})  # type: ignore[attr-defined]
    builder._registry = registry or _Registry()  # type: ignore[attr-defined]
    return builder


def _ceilings(*, cpu_cores: float | None = None, memory_bytes: int | None = None):
    """An async `spec_for` double.

    Async because the real one is: an item's size now depends on its OWNER's
    budget as well as its App's ceiling, and that is a store read. A synchronous
    double would be one that cannot express the contract it stands for."""

    async def _spec_for(_item: str) -> SandboxSpec:
        return SandboxSpec(cpu_cores=cpu_cores, memory_bytes=memory_bytes)

    return _spec_for


async def test_an_app_that_declares_a_third_party_tool_gets_it_resolved(monkeypatch) -> None:
    from workspace_app.api import turn_context

    monkeypatch.setattr(
        turn_context,
        "load_app_manifest",
        lambda slug: type(
            "M", (), {"agent": type("A", (), {"external_tools": {"wafer-history": "https://g/m"}})}
        ),
    )
    host = _Host()

    external = await _builder(slug="rca", sandbox=host)._external_tools("item-1", _Session())

    assert host.asked == [{"wafer-history": "https://g/m"}]
    assert external.shas == {"wafer-history": "a" * 64}
    assert [p.name for p in external.packages] == ["wafer-history"]


def _declaring(monkeypatch, **tools: str) -> None:
    from workspace_app.api import turn_context

    monkeypatch.setattr(
        turn_context,
        "load_app_manifest",
        lambda slug: type("M", (), {"agent": type("A", (), {"external_tools": dict(tools)})}),
    )


async def _resolve(host: _Host, item: str = "item-1", plugins: dict[str, str] | None = None):
    """Call the module function with this file's doubles.

    `Sandbox` and `ItemLocator` are cast rather than implemented: the function
    reaches for `resolve_tools` and `slug_of` and nothing else, and standing up
    the full surface of either would be a lot of code that tests nothing and
    hides which two methods actually matter here."""
    from workspace_app.api.turn_context import resolve_item_tools

    return await resolve_item_tools(
        cast("Sandbox", host),
        cast("ItemLocator", _Locator("rca")),
        item,
        plugin_artifacts=plugins or {},
    )


async def test_what_an_item_actually_got_is_recorded(monkeypatch, caplog) -> None:
    """#674 P8 / #724: the trail behind "that tool was behaving oddly".

    The URL points at the author's latest, so what ran can differ between two
    turns with nothing in the app changing. Resolve time is the only moment
    anything knows which bundle this was."""
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})

    with caplog.at_level("INFO", logger="workspace_app.api.turn_context"):
        await _resolve(_Host())

    (line,) = [r.getMessage() for r in caplog.records if "third-party tools" in r.getMessage()]
    assert "item-1" in line
    assert "wafer-history 1.4.2" in line
    assert "by Wafer Team <wafer@example.com>" in line
    assert "sha=aaaaaaaaaaaa" in line
    assert "LAST-KNOWN-GOOD" not in line


async def test_the_record_says_when_a_tool_came_from_the_cached_copy(monkeypatch, caplog) -> None:
    """A stale answer and a fresh one are the same bytes to everything
    downstream, and the difference is exactly what a person chasing "it used
    to work" needs."""
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})

    with caplog.at_level("INFO", logger="workspace_app.api.turn_context"):
        await _resolve(_Host(stale=True))

    (line,) = [r.getMessage() for r in caplog.records if "third-party tools" in r.getMessage()]
    assert "LAST-KNOWN-GOOD" in line


async def test_an_item_with_no_third_party_tools_records_nothing(monkeypatch, caplog) -> None:
    """Almost every item. A line per turn saying "none" would bury the ones
    that matter."""
    _declaring(monkeypatch)

    with caplog.at_level("INFO", logger="workspace_app.api.turn_context"):
        await _resolve(_Host())

    assert not [r for r in caplog.records if "third-party tools" in r.getMessage()]


async def test_an_item_with_no_app_asks_for_nothing() -> None:
    # A workflow or a bare item has no manifest to declare tools in; the turn
    # must not fabricate a lookup for it.
    host = _Host()

    external = await _builder(slug=None, sandbox=host)._external_tools("item-1", _Session())

    assert host.asked == []
    assert external.shas == {}


class _RecordingSandbox(MockSandbox):
    """Records the spec every `create` was called with.

    The assertion this file was missing is about what `create` RECEIVES, not
    about what the turn believed it had asked for — the two were free to
    disagree, and did."""

    def __init__(self) -> None:
        super().__init__()
        self.specs: list[SandboxSpec] = []

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.specs.append(spec)
        return await super().create(spec, sandbox_id)


async def test_the_shas_this_turn_resolved_reach_the_sandbox_it_creates() -> None:
    """#674's load-bearing invariant, tested at the seam that breaks it.

    Resolving at the top of a turn is only worth doing because the sandbox then
    mounts THOSE bundles. Schema and mount are different code paths, so a turn
    can resolve perfectly, hand the model a tool, and give it a launcher that
    does not exist — which is what `../.tools/<name>/launch: No such file or
    directory` is. The registry owns the item's ceilings and knows nothing about
    the turn, so the turn's answer has to travel WITH the wake."""
    sandbox = _RecordingSandbox()
    registry = InvestigationRegistry(
        sandbox=sandbox,
        # Mirrors `create_app._spec_for`: the App's resolved ceilings, looked up
        # per item, with no idea what this turn resolved.
        spec_for=_ceilings(cpu_cores=2.0, memory_bytes=1 << 30),
    )
    session = await registry.session("item-1")
    shas = {"wafer-history": "a" * 64}
    ctx = AgentToolContext(
        investigation_id="item-1",
        sandbox=sandbox,
        sandbox_spec=SandboxSpec(tools=shas),
        # Wired the way `TurnContextBuilder._common` wires it.
        ensure_sandbox_via=lambda on_progress, tools: registry.ensure_handle(
            session, tools=tools, on_progress=on_progress
        ),
    )

    await ctx.ensure_sandbox()

    assert sandbox.specs, "the turn never created a sandbox"
    created = sandbox.specs[-1]
    assert created.tools == shas, "the turn's third-party bundles never reached create"
    # The turn owns `tools`; the registry still owns everything else about this
    # item's sandbox, so carrying one must not flatten the other.
    assert created.cpu_cores == 2.0
    assert created.memory_bytes == 1 << 30


async def test_a_sandbox_woken_without_a_turn_still_mounts_the_items_tools() -> None:
    """What a sandbox mounts is a property of the ITEM, not of whoever woke it.

    Three of the four things that create one have no turn behind them — the
    human terminal (`POST …/exec`), a workflow's deterministic node, and the
    file-op rebuild — and a sandbox mounts its bundles exactly once, at create.
    So whichever of them happens to win the race after a restart must not get to
    decide that this item has no third-party tools for the rest of that
    sandbox's life. A turn still supplies its OWN shas, because those are pinned
    to the resolve whose schemas the model was given; everyone else asks."""
    sandbox = _RecordingSandbox()
    shas = {"wafer-history": "a" * 64}

    mounts = {"wafer-history": MountedTool(sha="a" * 64, version="1.2")}

    async def declared(_item_id: str) -> dict[str, MountedTool]:
        return dict(mounts)

    registry = InvestigationRegistry(
        sandbox=sandbox,
        spec_for=_ceilings(cpu_cores=2.0),
        tools_for=declared,
    )
    session = await registry.session("item-1")

    # The terminal / workflow / file-op shape: a wake with nothing to say about
    # tools.
    await registry.ensure_handle(session)

    assert sandbox.specs[-1].tools == shas, "a turn-less wake mounted no tools"
    assert session.tools == mounts
    assert sandbox.specs[-1].cpu_cores == 2.0


async def test_a_turn_that_states_its_tools_is_not_second_guessed() -> None:
    """An explicit `{}` is an answer, not a gap: an app that declares no
    third-party tools must not make every wake pay for a resolve."""
    sandbox = _RecordingSandbox()
    asked: list[str] = []

    async def declared(item_id: str) -> dict[str, MountedTool]:
        asked.append(item_id)
        return {"surprise": MountedTool(sha="b" * 64, version="9")}

    registry = InvestigationRegistry(sandbox=sandbox, tools_for=declared)
    session = await registry.session("item-1")

    await registry.ensure_handle(session, tools={})

    assert asked == []
    assert sandbox.specs[-1].tools == {}


# ── #847/#848: a view plugin's `{artifact: url}` sandbox half ──────────────


async def test_a_view_plugin_artifact_is_mounted_but_is_not_an_agent_tool(monkeypatch) -> None:
    """Its sha goes into the sandbox this item is created with — so the
    plugin's commands find their launcher — and nothing an agent or the tool
    picker reads mentions it."""
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    host = _Host()
    got = await _resolve(host, plugins={"chart": "https://g/chart"})
    assert host.asked == [{"chart": "https://g/chart", "wafer-history": "https://g/m"}]
    assert set(got.shas) == {"chart", "wafer-history"}
    assert [p.name for p in got.packages] == ["wafer-history"]


async def test_an_unresolvable_view_plugin_is_not_an_agent_facing_refusal(monkeypatch) -> None:
    _declaring(monkeypatch)

    class _Refusing(_Host):
        async def resolve_tools(self, declared: dict[str, str]) -> dict[str, Any]:
            return {"tools": {}, "refused": dict.fromkeys(declared, "artifact store unreachable")}

    got = await _resolve(_Refusing(), plugins={"chart": "https://g/chart"})
    assert got.refused == {}
    assert got.shas == {}


async def test_an_app_tool_of_the_same_name_wins_over_the_plugin(monkeypatch, caplog) -> None:
    _declaring(monkeypatch, chart="https://g/app-chart")
    host = _Host()
    with caplog.at_level("WARNING"):
        got = await _resolve(host, plugins={"chart": "https://g/plugin-chart"})
    assert host.asked == [{"chart": "https://g/app-chart"}]
    assert [p.name for p in got.packages] == ["chart"]
    assert "shadows the view plugin" in caplog.text


async def test_a_turn_on_a_live_sandbox_without_the_plugin_tells_the_agent_nothing(
    monkeypatch,
) -> None:
    """`confine_to_mounted` refuses what the live sandbox lacks; for a view
    plugin that refusal is the runner's to report, not the agent's to read."""
    _declaring(monkeypatch)
    builder = _builder(
        slug="rca", sandbox=_Host(), plugins={"chart": "https://g/chart"}, registry=_Registry({})
    )
    got = await builder._external_tools("item-1", _Session())
    assert got.refused == {}
    assert got.packages == ()


# ── plan-tool-running-version P3: the model is told the release that RUNS ──

from workspace_app.tooling.registry import describe_command  # noqa: E402

_LATEST = "a" * 64  # what `_Host` resolves, as release 1.4.2


def _live(tools: dict[str, MountedTool]) -> _Registry:
    """A live sandbox created with `tools`, as the registry reports it."""
    return _Registry(tools)


def _line(external) -> str:
    pkg = next(p for p in external.packages if p.name == "wafer-history")
    return describe_command(pkg, pkg.commands[0])


async def test_a_sandbox_older_than_the_release_is_described_as_what_it_runs(monkeypatch):
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    registry = _live({"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})

    external = await _builder(slug="rca", sandbox=_Host(), registry=registry)._external_tools(
        "item-1", _Session()
    )

    line = _line(external)
    assert "tool bundle 1.3.0" in line  # the release under /.tools, not the manifest's
    assert "1.4.2" in line  # ...and the latest is named
    assert "clos" in line.lower()  # ...with the way to get it
    # A sha says "different", not "older" (review round 1): no claim of order.
    assert "older" not in line and "since" not in line
    # The sandbox still mounts what it mounts: only the words change.
    assert external.shas == {"wafer-history": _LATEST}


async def test_a_sandbox_on_the_latest_release_says_nothing_more(monkeypatch):
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    fresh = await _builder(slug="rca", sandbox=_Host())._external_tools("item-1", _Session())
    registry = _live({"wafer-history": MountedTool(sha=_LATEST, version="1.4.2")})

    live = await _builder(slug="rca", sandbox=_Host(), registry=registry)._external_tools(
        "item-1", _Session()
    )

    assert _line(live) == _line(fresh)  # not one word added when nothing differs


async def test_no_live_sandbox_reads_as_the_latest(monkeypatch):
    # D4/D7: unknown, or nothing running, is the release the next sandbox gets.
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    external = await _builder(slug="rca", sandbox=_Host())._external_tools("item-1", _Session())
    assert "tool bundle 1.4.2" in _line(external)
    assert "clos" not in _line(external).lower()


async def test_a_peers_sandbox_is_described_from_its_address(monkeypatch):
    # D10: this pod holds no handle, a peer's sandbox is live — before, its
    # mounts were unknown here and the model was told the manifest's release.
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    registry = _Registry({"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})

    external = await _builder(slug="rca", sandbox=_Host(), registry=registry)._external_tools(
        "item-1", _Session()
    )

    assert "tool bundle 1.3.0" in _line(external)


async def test_a_peers_sandbox_without_a_tool_refuses_it_with_the_reason(monkeypatch):
    # The other half of D10: the mounted set is the ceiling on every pod now.
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    registry = _Registry({})

    external = await _builder(slug="rca", sandbox=_Host(), registry=registry)._external_tools(
        "item-1", _Session()
    )

    assert "wafer-history" in external.refused
    assert not external.packages


async def test_an_older_mount_with_no_recorded_release_is_still_called_older(monkeypatch):
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})
    registry = _live({"wafer-history": MountedTool(sha="b" * 64, version="")})

    external = await _builder(slug="rca", sandbox=_Host(), registry=registry)._external_tools(
        "item-1", _Session()
    )

    line = _line(external)
    assert "an unrecorded release" in line and "1.4.2" in line


async def test_a_real_chat_turn_carries_each_shas_release_to_the_sandbox_it_creates(monkeypatch):
    """Through the REAL wired builder (`create_app` → `build_chat_turn`): the
    ctx a turn runs with knows which release each sha is, so the sandbox it
    wakes records it (P2) — and the model is told the same release (P3)."""
    import workspace_app.api.app as app_mod
    from workspace_app.api import create_app, turn_context
    from workspace_app.api.events import RunDone
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.apps.playground.model import PlaygroundItem
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.resources import make_spec
    from workspace_app.tooling.external import ExternalTools, ToolProvenance

    async def resolved(*_a, **_k) -> ExternalTools:
        return ExternalTools(shas={"t": "s2"}, provenance={"t": ToolProvenance(version="2.0")})

    monkeypatch.setattr(turn_context, "resolve_item_tools", resolved)
    captured: dict[str, Any] = {}
    real = app_mod.WorkflowExecutor

    def _capture(**kw):
        captured["ex"] = real(**kw)
        return captured["ex"]

    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture)
    spec = make_spec()
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    item = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )

    async def _no_subagent(*_a, **_k):
        return "", []

    ctx = await captured["ex"]._turn_ctx.build_chat_turn(
        item,
        agent_config=None,
        run_subagent=_no_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
        conversation_id="c",
    )

    assert ctx.tool_versions == {"t": "2.0"}
    await ctx.ensure_sandbox(prepare_env=False)
    registry = captured["ex"]._turn_ctx._registry
    assert (await registry.session(item)).tools == {"t": MountedTool(sha="s2", version="2.0")}


async def test_an_app_with_no_third_party_tools_never_asks_what_was_mounted():
    # Review round 1: the turn build used to make no sandbox call; asking for
    # an app that declares nothing would put a host probe in every turn.
    registry = _Registry()
    await _builder(slug=None, sandbox=_Host(), registry=registry)._external_tools(
        "item-1", _Session()
    )
    assert registry.asked == []


async def test_a_latest_release_with_no_version_is_not_given_one(monkeypatch):
    # Nothing is invented in the latest's place (no "a newer release").
    _declaring(monkeypatch, **{"wafer-history": "https://g/m"})

    class _Unversioned(_Host):
        async def resolve_tools(self, declared):
            answer = await super().resolve_tools(declared)
            for described in answer["tools"].values():
                described["version"] = ""
            return answer

    registry = _live({"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})
    external = await _builder(
        slug="rca", sandbox=_Unversioned(), registry=registry
    )._external_tools("item-1", _Session())
    line = _line(external)
    assert "runs 1.3.0, not the latest release." in line
