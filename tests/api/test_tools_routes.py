"""#322: the tool routes — the flat catalog (for chat tool cards) and the
per-item picker state (per-tool tri-state, resolved server-side so the FE never
recomputes the default set and drifts from a real turn).

#724 puts a third-party tool's release and author on the tool's OWN row: its
`external_tools` key is an `app.json` `tools[]` entry like any other, so it
already had a row with a switch — describing it anywhere else listed the same
tool twice."""

from workspace_app.tooling.external import ExternalTools, ToolProvenance

from .conftest import Harness, register_rca_item


def test_tools_catalog_lists_builtins_with_human_labels(harness: Harness):
    rows = harness.client.get("/tools").json()
    by_name = {r["name"]: r for r in rows}
    assert by_name["ask_knowledge_base"]["label"] == "Ask Knowledge Base"
    assert by_name["exec"]["description"].startswith("Run a shell command")


def test_item_tools_reports_per_tool_tristate_state(harness: Harness):
    rows = harness.client.get(harness.wpath("/tools")).json()["tools"]
    by_key = {r["key"]: r for r in rows}
    # the App ceiling is offered, one pickable unit per app.json tools[] entry
    assert "exec" in by_key and "rca-tools" in by_key
    exec_row = by_key["exec"]
    assert exec_row["label"] == "Exec"
    assert exec_row["pref"] == "follow"  # no per-item override yet
    assert exec_row["default_on"] is True  # in the (default-profile) default set
    assert exec_row["effective"] is True


def test_item_tools_reflects_a_forced_off_pref(harness: Harness):
    iid = register_rca_item(harness.spec, attached_tool_prefs={"rca-tools": False})
    rows = harness.client.get(f"/a/rca/items/{iid}/tools").json()["tools"]
    by_key = {r["key"]: r for r in rows}
    assert by_key["rca-tools"]["pref"] == "off"
    assert by_key["rca-tools"]["effective"] is False
    assert by_key["exec"]["effective"] is True  # untouched → still on


def test_item_tools_unknown_item_is_404(harness: Harness):
    assert harness.client.get("/a/rca/items/nope/tools").status_code == 404


def _resolving(monkeypatch, answer: ExternalTools) -> list[str]:
    """Stand in for the host round-trip, and record which item was asked
    about — a row that described a DIFFERENT item's tools would still render
    perfectly."""
    from workspace_app.api import tools_routes

    asked: list[str] = []

    async def _fake(sandbox, locator, item_id):
        asked.append(item_id)
        return answer

    monkeypatch.setattr(tools_routes, "resolve_item_tools", _fake)
    return asked


def _declaring(monkeypatch, *names: str) -> None:
    """An app whose `tools[]` ceiling holds these third-party entries — which
    is where an `external_tools` key belongs, and is why they have a row."""
    from workspace_app.api import tools_routes

    monkeypatch.setattr(
        tools_routes,
        "load_app_manifest",
        lambda slug: type(
            "M",
            (),
            {
                "agent": type(
                    "A",
                    (),
                    {
                        "tools": ["exec", *names],
                        "external_tools": {n: "https://g/m" for n in names},
                    },
                )
            },
        ),
    )


def _resolved(**over) -> ExternalTools:
    from workspace_app.tooling.registry import CommandInfo, PackageInfo

    return ExternalTools(
        packages=(
            PackageInfo(
                name="wafer-history",
                install_dir="../.tools/wafer-history",
                commands=(CommandInfo("trend", "Yield trend for a lot.", {}),),
            ),
        ),
        shas={"wafer-history": "a" * 64},
        provenance={
            "wafer-history": ToolProvenance(
                version="1.4.2", author="Wafer Team <wafer@example.com>", **over
            )
        },
    )


def test_a_third_party_tool_carries_its_release_and_author_on_its_own_row(
    harness: Harness, monkeypatch
):
    """#724. An app tool ships with us and our own code describes it; a
    third-party tool is somebody else's release, changing on their schedule.
    Both are rows in the same list, because both are tools this item may use —
    the third-party one just has more to say about where it came from."""
    iid = register_rca_item(harness.spec)
    _declaring(monkeypatch, "wafer-history")
    asked = _resolving(monkeypatch, _resolved())

    rows = harness.client.get(f"/a/rca/items/{iid}/tools").json()["tools"]

    assert asked == [iid]
    by_key = {r["key"]: r for r in rows}
    row = by_key["wafer-history"]
    assert row["version"] == "1.4.2"
    assert row["author"] == "Wafer Team <wafer@example.com>"
    assert row["stale"] is False
    assert row["unavailable"] is None
    # It is a picker row like any other: it has a switch and a tri-state.
    assert row["pref"] == "follow"
    # And now a description, because resolving told us what it bundles. The
    # bare `tools[]` entry alone could only produce an empty one.
    assert "Trend" in row["description"]


def test_a_first_party_tool_claims_no_release_or_author(harness: Harness, monkeypatch):
    """The fields are per-row rather than per-list, so the ones that ship with
    the platform have to say nothing rather than say something wrong."""
    iid = register_rca_item(harness.spec)
    _declaring(monkeypatch, "wafer-history")
    _resolving(monkeypatch, _resolved())

    rows = harness.client.get(f"/a/rca/items/{iid}/tools").json()["tools"]

    exec_row = {r["key"]: r for r in rows}["exec"]
    assert exec_row["version"] is None
    assert exec_row["author"] is None
    assert exec_row["stale"] is False
    assert exec_row["unavailable"] is None


def test_a_third_party_row_says_it_came_from_the_cached_copy(harness: Harness, monkeypatch):
    """The bytes are usable, so nothing refuses them — but "this is not
    necessarily the latest" is what the person reading the version needs."""
    iid = register_rca_item(harness.spec)
    _declaring(monkeypatch, "wafer-history")
    _resolving(monkeypatch, _resolved(stale=True))

    rows = harness.client.get(f"/a/rca/items/{iid}/tools").json()["tools"]

    assert {r["key"]: r for r in rows}["wafer-history"]["stale"] is True


def test_a_third_party_row_that_could_not_be_resolved_says_why(harness: Harness, monkeypatch):
    """#480's shape, on the row itself. The tool is still in `tools[]`, so it
    still has a switch — what it has lost is the ability to run, and a row that
    looked ordinary would leave someone toggling it and wondering."""
    iid = register_rca_item(harness.spec)
    _declaring(monkeypatch, "legacy-fetch")
    _resolving(monkeypatch, ExternalTools(refused={"legacy-fetch": "404 — the artifact expired"}))

    rows = harness.client.get(f"/a/rca/items/{iid}/tools").json()["tools"]

    row = {r["key"]: r for r in rows}["legacy-fetch"]
    assert row["unavailable"] == "404 — the artifact expired"
    assert row["version"] is None


def test_no_tool_is_listed_twice(harness: Harness, monkeypatch):
    """The bug this replaced: a separate third-party section listed the same
    tool a second time, under the row that already had its switch."""
    iid = register_rca_item(harness.spec)
    _declaring(monkeypatch, "wafer-history")
    _resolving(monkeypatch, _resolved())

    body = harness.client.get(f"/a/rca/items/{iid}/tools").json()

    keys = [r["key"] for r in body["tools"]]
    assert len(keys) == len(set(keys))
    assert "external" not in body  # nowhere else for a tool to appear


def test_item_tools_costs_no_host_call_when_an_app_declares_no_third_party_tools(
    harness: Harness,
):
    """The common case, and it must stay free."""
    rows = harness.client.get(harness.wpath("/tools")).json()["tools"]

    assert all(r["version"] is None and r["unavailable"] is None for r in rows)


def test_item_tools_still_answers_when_the_third_party_host_is_unreachable(
    harness: Harness, monkeypatch
):
    """The pickable App tools have nothing to do with any artifact store. One
    unreachable host must not take away the switches someone opened this modal
    to press — it may only cost those rows their provenance."""
    from workspace_app.api import tools_routes

    async def _boom(sandbox, locator, item_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tools_routes, "resolve_item_tools", _boom)
    _declaring(monkeypatch, "wafer-history")
    iid = register_rca_item(harness.spec)

    r = harness.client.get(f"/a/rca/items/{iid}/tools")

    assert r.status_code == 200
    by_key = {row["key"]: row for row in r.json()["tools"]}
    assert "connection refused" in by_key["wafer-history"]["unavailable"]
    assert by_key["exec"]["unavailable"] is None  # untouched
