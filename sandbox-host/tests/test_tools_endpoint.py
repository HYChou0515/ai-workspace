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
from sandbox_host.artifact import CommandSpec
from sandbox_host.tool_resolve import FetchError, ResolvedTool


class _Resolver:
    def __init__(self, **answers: object) -> None:
        self.answers = answers
        self.seen: list[tuple[str, str]] = []

    def resolve(self, name: str, url: str) -> ResolvedTool:
        self.seen.append((name, url))
        answer = self.answers[name]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _tool(name: str, *, stale: bool = False) -> ResolvedTool:
    return ResolvedTool(
        name=name,
        sha="a" * 64,
        version="1.4.2",
        commands=(CommandSpec("trend", "Yield trend.", {"type": "object"}),),
        stale=stale,
    )


def _client(resolver) -> httpx.AsyncClient:
    app = make_host_app(object(), advertise_url="http://h", tool_resolver=resolver)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


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
    assert tool["commands"] == [
        {"name": "trend", "description": "Yield trend.", "params_json_schema": {"type": "object"}}
    ]
    assert resolver.seen == [("wafer-history", "https://g/m")]


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
