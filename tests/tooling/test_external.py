"""P8 — third-party tools, from an app's declaration to the model (#674).

An app declares `{local name: artifact url}`. Once per turn the app asks the
host to resolve them, and the same answer feeds two places at once: the tool
definitions the model is given, and the `{name: sha}` the sandbox is created
with. One answer, so the interface the model was told about is always the
bundle that actually runs.
"""

from __future__ import annotations

from workspace_app.tooling.external import (
    ExternalTools,
    confine_to_mounted,
    prewarm_external_tools,
    resolve_external_tools,
)
from workspace_app.tooling.registry import PackageInfo


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
            "author": "Wafer Team <wafer@example.com>",
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

    assert external.provenance["wafer-history"].stale is True
    assert external.shas == {"wafer-history": "a" * 64}  # still usable


async def test_the_release_and_its_author_survive_the_resolve() -> None:
    """#724: the host answers with both and the app used to keep neither, so
    "which version is this and who wrote it" had no answer anywhere above the
    host — including for the person the tool just misbehaved for."""
    external = await resolve_external_tools(_Host(_ANSWER), {"wafer-history": "https://g/m"})

    got = external.provenance["wafer-history"]
    assert got.version == "1.4.2"
    assert got.author == "Wafer Team <wafer@example.com>"
    assert got.stale is False


async def test_a_tool_published_without_an_author_still_resolves() -> None:
    """Every bundle built before the builder wrote the field. Dropping the key
    must not be the thing that fails a turn."""
    answer = {
        "tools": {
            "wafer-history": {
                k: v for k, v in _ANSWER["tools"]["wafer-history"].items() if k != "author"
            }
        },
        "refused": {},
    }

    external = await resolve_external_tools(_Host(answer), {"wafer-history": "u"})

    assert external.provenance["wafer-history"].author is None
    assert external.shas == {"wafer-history": "a" * 64}


async def test_confining_to_what_is_mounted_drops_the_provenance_too() -> None:
    """A tool the live sandbox never mounted is not running, so claiming a
    version for it would be describing something that is not there."""
    external = await resolve_external_tools(_Host(_ANSWER), {"wafer-history": "u"})

    confined = confine_to_mounted(external, live=True, mounted={})

    assert confined.provenance == {}
    assert "wafer-history" in confined.refused


async def test_prewarm_pulls_every_apps_tools_into_the_cache() -> None:
    host = _Host(_ANSWER)

    unwarmed = await prewarm_external_tools(
        host, {"rca": {"wafer-history": "https://g/m"}, "pm": {}}
    )

    assert host.asked == [{"wafer-history": "https://g/m"}]  # the empty app is skipped
    assert unwarmed == {}


async def test_a_store_outage_at_boot_does_not_stop_the_pod_starting() -> None:
    # Q17: the opposite of first-party discovery, which IS fail-loud — those
    # bundles are inside our own image, so their absence means a broken build.
    # A third-party store is someone else's uptime.
    class _Exploding:
        resolves_tools = True

        async def resolve_tools(self, _declared):
            raise ConnectionError("gitlab unreachable")

    unwarmed = await prewarm_external_tools(_Exploding(), {"rca": {"wafer-history": "u"}})

    assert unwarmed == {"wafer-history": "gitlab unreachable"}


async def test_prewarm_reports_what_will_be_missing_rather_than_staying_quiet() -> None:
    host = _Host({"tools": {}, "refused": {"legacy": "404 — the artifact expired"}})

    unwarmed = await prewarm_external_tools(host, {"rca": {"legacy": "u"}})

    assert unwarmed == {"legacy": "404 — the artifact expired"}


def _resolved(**shas: str) -> ExternalTools:
    return ExternalTools(
        packages=tuple(
            PackageInfo(name=n, install_dir=f"../.tools/{n}", commands=()) for n in shas
        ),
        shas=dict(shas),
    )


def test_a_sandbox_that_predates_a_tool_does_not_get_it_offered() -> None:
    """A sandbox mounts its bundles when it is CREATED, so a tool registered
    while one was already up has no launcher in it. Offering it anyway is what
    turns an operator's successful rollout into `No such file or directory` —
    a message that names neither the tool nor the reason."""
    confined = confine_to_mounted(_resolved(wafer="a" * 64), live=True, mounted={})

    assert confined.shas == {}
    assert [p.name for p in confined.packages] == []
    assert "wafer" in confined.refused
    assert "restart" in confined.refused["wafer"] or "recycle" in confined.refused["wafer"]


def test_a_mounted_tool_is_offered_normally() -> None:
    external = _resolved(wafer="a" * 64)

    confined = confine_to_mounted(external, live=True, mounted={"wafer": "a" * 64})

    assert confined.shas == {"wafer": "a" * 64}
    assert [p.name for p in confined.packages] == ["wafer"]
    assert confined.refused == {}


def test_a_sandbox_holding_an_older_build_keeps_the_tool() -> None:
    """The author released between this sandbox being created and this turn
    resolving. That is the documented no-op path — they push, the NEXT sandbox
    gets it — so the old bundle keeps serving this session rather than the tool
    vanishing mid-conversation. Confinement is about a launcher that does not
    exist, not about being a release behind."""
    external = _resolved(wafer="b" * 64)

    confined = confine_to_mounted(external, live=True, mounted={"wafer": "a" * 64})

    assert confined is external
    assert confined.refused == {}


def test_nothing_is_confined_before_the_sandbox_exists() -> None:
    # The common case: no sandbox yet, so THIS turn's create is what mounts
    # them. Confining here would refuse every tool on every cold item.
    external = _resolved(wafer="a" * 64)

    assert confine_to_mounted(external, live=False, mounted=None) is external


def test_an_unknown_mounted_set_is_left_alone_rather_than_guessed() -> None:
    """Another pod created this sandbox (#366 address convergence), so this one
    never learned what it mounted. `None` means UNKNOWN, not empty — refusing on
    a guess would take working tools away from every multi-pod deployment."""
    external = _resolved(wafer="a" * 64)

    assert confine_to_mounted(external, live=True, mounted=None) is external
