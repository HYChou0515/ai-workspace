"""#322: the tool routes — the flat catalog (for chat tool cards) and the
per-item picker state (per-tool tri-state, resolved server-side so the FE never
recomputes the default set and drifts from a real turn)."""

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
