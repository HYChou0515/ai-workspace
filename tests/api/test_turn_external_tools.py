"""P8 — an app's third-party tools reach the turn that will use them (#674).

`TurnContextBuilder` takes ~20 collaborators, and the behaviour under test
needs two of them, so the method is exercised directly rather than through a
whole app. What matters is the wiring: the app's declaration is read from its
manifest, resolved against the backend, and the answer arrives whole.
"""

from __future__ import annotations

from typing import Any

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.registry import InvestigationRegistry
from workspace_app.api.turn_context import TurnContextBuilder
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


class _Session:
    """A registry session with no sandbox yet — the cold case, where this
    turn's create is what mounts the bundles."""

    handle = None
    tools: dict[str, str] | None = None


class _Locator:
    def __init__(self, slug: str | None) -> None:
        self._slug = slug

    def slug_of(self, _item_id: str) -> str | None:
        return self._slug


class _Host:
    resolves_tools = True

    def __init__(self) -> None:
        self.asked: list[dict[str, str]] = []

    async def resolve_tools(self, declared: dict[str, str]) -> dict[str, Any]:
        self.asked.append(declared)
        return {
            "tools": {
                name: {
                    "sha": "a" * 64,
                    "version": "1.4.2",
                    "stale": False,
                    "commands": [{"name": "trend", "description": "d", "params_json_schema": {}}],
                }
                for name in declared
            },
            "refused": {},
        }


def _builder(*, slug: str | None, sandbox: object) -> TurnContextBuilder:
    builder = object.__new__(TurnContextBuilder)
    builder._locator = _Locator(slug)  # type: ignore[attr-defined]
    builder._sandbox = sandbox  # type: ignore[attr-defined]
    return builder


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
        spec_for=lambda _item: SandboxSpec(cpu_cores=2.0, memory_bytes=1 << 30),
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

    async def declared(_item_id: str) -> dict[str, str]:
        return dict(shas)

    registry = InvestigationRegistry(
        sandbox=sandbox,
        spec_for=lambda _item: SandboxSpec(cpu_cores=2.0),
        tools_for=declared,
    )
    session = await registry.session("item-1")

    # The terminal / workflow / file-op shape: a wake with nothing to say about
    # tools.
    await registry.ensure_handle(session)

    assert sandbox.specs[-1].tools == shas, "a turn-less wake mounted no tools"
    assert session.tools == shas
    assert sandbox.specs[-1].cpu_cores == 2.0


async def test_a_turn_that_states_its_tools_is_not_second_guessed() -> None:
    """An explicit `{}` is an answer, not a gap: an app that declares no
    third-party tools must not make every wake pay for a resolve."""
    sandbox = _RecordingSandbox()
    asked: list[str] = []

    async def declared(item_id: str) -> dict[str, str]:
        asked.append(item_id)
        return {"surprise": "b" * 64}

    registry = InvestigationRegistry(sandbox=sandbox, tools_for=declared)
    session = await registry.session("item-1")

    await registry.ensure_handle(session, tools={})

    assert asked == []
    assert sandbox.specs[-1].tools == {}
