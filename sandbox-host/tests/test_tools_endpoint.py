"""P6 — `POST /tools/resolve`, the app's only route to a third-party tool.

The app holds no artifact-store credential and never fetches a manifest; it
asks the host, and gets back the sha to mount plus the metadata to describe
the tool to the model. Both come from one act, so the two can't disagree.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.artifact import CommandSpec, EnvSpec
from sandbox_host.tool_resolve import FetchError, ResolvedTool


class _Resolver:
    #: The real resolver exposes its cache so the host can sweep it; a double
    #: that omitted it would pass while production raised.
    cache = None

    def __init__(self, **answers: object) -> None:
        self.answers = answers
        self.seen: list[tuple[str, str]] = []

    def resolve(self, name: str, url: str) -> ResolvedTool:
        self.seen.append((name, url))
        answer = self.answers[name]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _tool(name: str, *, stale: bool = False, author: str | None = None) -> ResolvedTool:
    return ResolvedTool(
        name=name,
        sha="a" * 64,
        version="1.4.2",
        commands=(CommandSpec("trend", "Yield trend.", {"type": "object"}),),
        stale=stale,
        author=author,
    )


def _client(resolver) -> httpx.AsyncClient:
    app = make_host_app(object(), advertise_url="http://h", tool_resolver=resolver)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


async def test_resolve_passes_on_what_the_tool_says_it_needs_from_the_environment() -> None:
    """#750 — the author's `env.json` reaches the app through this route.

    The app never reads a manifest, so anything this response drops is gone for
    good: the declaration would sit in the bundle while every third-party tool
    reported "did not say" to the panel that exists to answer exactly that.
    Same shape of loss as the `tools` field the host once dropped (#696)."""
    resolver = _Resolver(
        **{
            "wafer-history": ResolvedTool(
                name="wafer-history",
                sha="a" * 64,
                version="1.4.2",
                commands=(CommandSpec("trend", "Yield trend.", {"type": "object"}),),
                stale=False,
                env=(EnvSpec("WAFER_API", "Yield service", True), EnvSpec("WAFER_CACHE")),
            )
        }
    )

    async with _client(resolver) as c:
        r = await c.post("/tools/resolve", json={"tools": {"wafer-history": "https://g/m"}})

    assert r.status_code == 200, r.text
    assert r.json()["tools"]["wafer-history"]["env"] == [
        {"name": "WAFER_API", "description": "Yield service", "required": True},
        {"name": "WAFER_CACHE", "description": "", "required": None},
    ]


async def test_resolve_omits_env_entirely_when_the_tool_declared_none() -> None:
    """Absent, not empty. Every artifact published before #750 lands here, and
    an empty list would tell the panel "needs nothing" about all of them."""
    resolver = _Resolver(**{"wafer-history": _tool("wafer-history")})

    async with _client(resolver) as c:
        r = await c.post("/tools/resolve", json={"tools": {"wafer-history": "https://g/m"}})

    assert "env" not in r.json()["tools"]["wafer-history"]


async def test_resolve_answers_with_the_sha_to_mount_and_the_schema_to_publish() -> None:
    resolver = _Resolver(**{"wafer-history": _tool("wafer-history")})

    async with _client(resolver) as c:
        r = await c.post("/tools/resolve", json={"tools": {"wafer-history": "https://g/m"}})

    assert r.status_code == 200
    body = r.json()
    assert body["refused"] == {}
    tool = body["tools"]["wafer-history"]
    assert tool["sha"] == "a" * 64
    assert tool["version"] == "1.4.2"
    assert tool["stale"] is False
    assert tool["author"] is None
    assert tool["commands"] == [
        {"name": "trend", "description": "Yield trend.", "params_json_schema": {"type": "object"}}
    ]
    assert resolver.seen == [("wafer-history", "https://g/m")]


async def test_resolve_names_the_author_so_the_app_can_show_who_to_ask() -> None:
    """#724. The app never reads a manifest, so a field the host does not
    forward is a field that does not exist for anyone downstream."""
    resolver = _Resolver(**{"wafer-history": _tool("wafer-history", author="Wafer Team <w@x>")})

    async with _client(resolver) as c:
        r = await c.post("/tools/resolve", json={"tools": {"wafer-history": "https://g/m"}})

    assert r.json()["tools"]["wafer-history"]["author"] == "Wafer Team <w@x>"


async def test_one_broken_tool_does_not_take_the_others_down() -> None:
    # The app turns this into "that tool is missing, and here is why", and the
    # turn still runs. A 500 here would mean one author's expired artifact
    # silently disables every other tool in the workspace.
    resolver = _Resolver(
        **{"good": _tool("good"), "broken": FetchError("404 — the artifact expired")}
    )

    async with _client(resolver) as c:
        r = await c.post(
            "/tools/resolve",
            json={"tools": {"good": "https://g/m", "broken": "https://b/m"}},
        )

    assert r.status_code == 200
    body = r.json()
    assert set(body["tools"]) == {"good"}
    assert "expired" in body["refused"]["broken"]


async def test_a_host_with_no_tool_store_says_so_instead_of_pretending() -> None:
    async with _client(None) as c:
        r = await c.post("/tools/resolve", json={"tools": {"wafer-history": "https://g/m"}})

    assert r.status_code == 200
    assert r.json()["tools"] == {}
    assert "tool store" in r.json()["refused"]["wafer-history"]


@pytest.mark.parametrize("stale", [True, False])
async def test_a_stale_answer_is_reported_as_such(stale: bool) -> None:
    resolver = _Resolver(**{"t": _tool("t", stale=stale)})

    async with _client(resolver) as c:
        r = await c.post("/tools/resolve", json={"tools": {"t": "https://g/m"}})

    assert r.json()["tools"]["t"]["stale"] is stale


class _Cache:
    def __init__(self) -> None:
        self.swept: list[tuple[set[str], int | None]] = []

    def sweep(self, *, in_use: set[str], max_bytes: int | None = None) -> list[str]:
        self.swept.append((in_use, max_bytes))
        return ["b" * 64]


async def test_the_sweeper_asks_the_live_sandboxes_what_they_are_running() -> None:
    """A bundle stops being referenced when a sandbox ends, which is why this
    runs beside the idle reaper. The in-use set comes from the live views, so
    a bundle a turn is using now cannot be evicted however full the cache."""

    class _Backend:
        def tools_in_use(self) -> set[str]:
            return {"a" * 64}

    resolver = _Resolver()
    resolver.cache = _Cache()
    app = make_host_app(_Backend(), advertise_url="http://h", tool_resolver=resolver)

    removed = await app.state.controller.sweep_tool_cache(max_bytes=999)

    assert removed == ["b" * 64]
    assert resolver.cache.swept == [({"a" * 64}, 999)]


async def test_a_host_with_no_tool_store_sweeps_nothing() -> None:
    app = make_host_app(object(), advertise_url="http://h", tool_resolver=None)

    assert await app.state.controller.sweep_tool_cache() == []


class _RecordingSandbox:
    """Records the spec `create` was handed, so a field dropped between the
    app's request body and the sandbox is visible."""

    def __init__(self) -> None:
        self.spec: object = None

    async def create(self, spec):
        from sandbox_host.protocol import SandboxHandle

        self.spec = spec
        return SandboxHandle(id="rid-1")


async def test_create_mounts_the_third_party_bundles_the_app_resolved() -> None:
    """#674: the app sends `{name: sha}` on create and the sandbox mounts them.

    Regressions here are invisible: the schemas still reach the model (they
    come from `/tools/resolve`, a different call), so the tool LOOKS present
    and every call fails with "launcher not found". Pydantic drops an unknown
    field silently, so only asserting the far side catches it."""
    sandbox = _RecordingSandbox()
    app = make_host_app(sandbox, advertise_url="http://h")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        r = await c.post(
            "/sandboxes", json={"item_id": "i-1", "tools": {"wafer-history": "a" * 64}}
        )

    assert r.status_code == 200
    assert sandbox.spec.tools == {"wafer-history": "a" * 64}
