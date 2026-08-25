"""#322: the tool routes — the flat catalog (for chat tool cards) and the
per-item picker state (per-tool tri-state, resolved server-side so the FE never
recomputes the default set and drifts from a real turn).

#724 adds the third-party section: disclosure, not a switch — see
`ExternalToolState`."""

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
    about — a section that described a DIFFERENT item's tools would still
    render perfectly."""
    from workspace_app.api import tools_routes

    asked: list[str] = []

    async def _fake(sandbox, locator, item_id):
        asked.append(item_id)
        return answer

    monkeypatch.setattr(tools_routes, "resolve_item_tools", _fake)
    return asked


def test_item_tools_names_the_release_and_author_of_each_third_party_tool(
    harness: Harness, monkeypatch
):
    """#724: an app tool ships with us and is described by our own code. A
    third-party tool is somebody else's release, changing on their schedule,
    and until now nothing said whose or which."""
    iid = register_rca_item(harness.spec)
    asked = _resolving(
        monkeypatch,
        ExternalTools(
            shas={"wafer-history": "a" * 64},
            provenance={
                "wafer-history": ToolProvenance(
                    version="1.4.2", author="Wafer Team <wafer@example.com>"
                )
            },
        ),
    )

    body = harness.client.get(f"/a/rca/items/{iid}/tools").json()

    assert asked == [iid]
    assert body["external"] == [
        {
            "key": "wafer-history",
            "version": "1.4.2",
            "author": "Wafer Team <wafer@example.com>",
            "stale": False,
            "unavailable": None,
        }
    ]


def test_item_tools_says_a_third_party_tool_is_running_from_a_cached_copy(
    harness: Harness, monkeypatch
):
    """The bytes are usable, so nothing refuses them — but "this is not
    necessarily the latest" is precisely what the person reading the version
    number needs to know about it."""
    iid = register_rca_item(harness.spec)
    _resolving(
        monkeypatch,
        ExternalTools(
            shas={"wafer-history": "a" * 64},
            provenance={"wafer-history": ToolProvenance(version="1.4.2", stale=True)},
        ),
    )

    (row,) = harness.client.get(f"/a/rca/items/{iid}/tools").json()["external"]

    assert row["stale"] is True
    assert row["version"] == "1.4.2"
    assert row["author"] is None


def test_item_tools_lists_a_third_party_tool_that_could_not_be_resolved(
    harness: Harness, monkeypatch
):
    """#480's shape. A tool the app declares but nobody can get is the case a
    person most needs named — dropping it from the list makes an outage look
    like a configuration they imagined."""
    iid = register_rca_item(harness.spec)
    _resolving(
        monkeypatch,
        ExternalTools(refused={"legacy-fetch": "404 — the artifact expired"}),
    )

    (row,) = harness.client.get(f"/a/rca/items/{iid}/tools").json()["external"]

    assert row["key"] == "legacy-fetch"
    assert row["unavailable"] == "404 — the artifact expired"
    assert row["version"] is None


def test_item_tools_has_no_third_party_section_when_an_app_declares_none(harness: Harness):
    """The common case, and it must cost nothing: no host round-trip, no empty
    heading in the picker."""
    assert harness.client.get(harness.wpath("/tools")).json()["external"] == []


def test_item_tools_still_answers_when_the_third_party_host_is_unreachable(
    harness: Harness, monkeypatch
):
    """The pickable App tools have nothing to do with any artifact store. One
    unreachable host must not take away the switches someone opened this modal
    to press — it may only cost the section that describes third-party tools."""
    from workspace_app.api import tools_routes

    async def _boom(sandbox, locator, item_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tools_routes, "resolve_item_tools", _boom)
    monkeypatch.setattr(
        tools_routes,
        "load_app_manifest",
        lambda slug: type(
            "M",
            (),
            {
                "agent": type(
                    "A", (), {"tools": [], "external_tools": {"wafer-history": "https://g/m"}}
                )
            },
        ),
    )
    iid = register_rca_item(harness.spec)

    r = harness.client.get(f"/a/rca/items/{iid}/tools")

    assert r.status_code == 200
    (row,) = r.json()["external"]
    assert row["key"] == "wafer-history"
    assert "connection refused" in row["unavailable"]
