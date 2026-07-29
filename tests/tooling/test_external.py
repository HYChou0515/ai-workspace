"""P8 — third-party tools, from an app's declaration to the model (#674).

An app declares `{local name: artifact url}`. Once per turn the app asks the
host to resolve them, and the same answer feeds two places at once: the tool
definitions the model is given, and the `{name: sha}` the sandbox is created
with. One answer, so the interface the model was told about is always the
bundle that actually runs.
"""

from __future__ import annotations

from workspace_app.tooling.external import resolve_external_tools


class _Host:
    """A sandbox that can resolve tools — i.e. the hosted backend."""

    resolves_tools = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.asked: list[dict[str, str]] = []

    async def resolve_tools(self, declared: dict[str, str]) -> dict:
        self.asked.append(declared)
        return self.payload


class _Plain:
    """A sandbox with no artifact store behind it (local dev)."""

    resolves_tools = False


_ANSWER = {
    "tools": {
        "wafer-history": {
            "sha": "a" * 64,
            "version": "1.4.2",
            "stale": False,
            "commands": [
                {
                    "name": "trend",
                    "description": "Yield trend for a lot.",
                    "params_json_schema": {"type": "object", "properties": {}},
                }
            ],
        }
    },
    "refused": {},
}


async def test_a_resolved_tool_becomes_a_package_the_agent_can_call() -> None:
    host = _Host(_ANSWER)

    external = await resolve_external_tools(host, {"wafer-history": "https://g/m"})

    assert host.asked == [{"wafer-history": "https://g/m"}]
    (pkg,) = external.packages
    assert pkg.name == "wafer-history"
    # The same sandbox-relative shape first-party packages use: the sandbox is
    # shown the NAME, and the sha never appears in a path the agent sees.
    assert pkg.install_dir == "../.tools/wafer-history"
    assert [c.name for c in pkg.commands] == ["trend"]
    assert pkg.commands[0].params_json_schema == {"type": "object", "properties": {}}


async def test_the_sha_travels_to_the_sandbox_that_will_run_it() -> None:
    external = await resolve_external_tools(_Host(_ANSWER), {"wafer-history": "https://g/m"})

    assert external.shas == {"wafer-history": "a" * 64}


async def test_nothing_is_asked_when_an_app_declares_no_third_party_tools() -> None:
    host = _Host(_ANSWER)

    external = await resolve_external_tools(host, {})

    assert host.asked == []
    assert external.packages == ()
    assert external.shas == {}


async def test_a_refused_tool_is_dropped_with_its_reason_kept() -> None:
    # The turn still runs. #480's shape: a tool that is not there should say
    # why, or a user is left guessing whether they asked wrong.
    host = _Host(
        {
            "tools": _ANSWER["tools"],
            "refused": {"legacy-fetch": "404 — the artifact expired"},
        }
    )

    external = await resolve_external_tools(host, {"wafer-history": "u", "legacy-fetch": "v"})

    assert [p.name for p in external.packages] == ["wafer-history"]
    assert external.refused == {"legacy-fetch": "404 — the artifact expired"}


async def test_a_backend_with_no_artifact_store_says_so_per_tool() -> None:
    external = await resolve_external_tools(_Plain(), {"wafer-history": "u"})

    assert external.packages == ()
    assert "hosted sandbox" in external.refused["wafer-history"]


async def test_a_tool_served_from_the_last_known_good_copy_is_flagged() -> None:
    answer = {
        "tools": {
            "wafer-history": {**_ANSWER["tools"]["wafer-history"], "stale": True},
        },
        "refused": {},
    }

    external = await resolve_external_tools(_Host(answer), {"wafer-history": "u"})

    assert external.stale == ("wafer-history",)
    assert external.shas == {"wafer-history": "a" * 64}  # still usable
