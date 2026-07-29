"""P8 — an app's third-party tools reach the turn that will use them (#674).

`TurnContextBuilder` takes ~20 collaborators, and the behaviour under test
needs two of them, so the method is exercised directly rather than through a
whole app. What matters is the wiring: the app's declaration is read from its
manifest, resolved against the backend, and the answer arrives whole.
"""

from __future__ import annotations

from typing import Any

from workspace_app.api.turn_context import TurnContextBuilder


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

    external = await _builder(slug="rca", sandbox=host)._external_tools("item-1")

    assert host.asked == [{"wafer-history": "https://g/m"}]
    assert external.shas == {"wafer-history": "a" * 64}
    assert [p.name for p in external.packages] == ["wafer-history"]


async def test_an_item_with_no_app_asks_for_nothing() -> None:
    # A workflow or a bare item has no manifest to declare tools in; the turn
    # must not fabricate a lookup for it.
    host = _Host()

    external = await _builder(slug=None, sandbox=host)._external_tools("item-1")

    assert host.asked == []
    assert external.shas == {}
