from workspace_app.apps.manifest import load_app_manifest


def test_load_rca_app_manifest():
    """The RCA App's `app.json` decodes into a typed `AppManifest` carrying its
    identity, function toggles, agent ceiling (picker + tools + base prompt),
    item nouns, layout, and labels."""
    m = load_app_manifest("rca")

    # identity
    assert m.slug == "rca"
    assert m.title
    assert m.color  # a hex for the per-app re-theme

    # function toggles
    assert m.function.workspace is True
    assert m.function.sandbox is True
    assert m.function.terminal is True

    # agent ceiling
    assert [p.preset for p in m.agent.picker] == ["qwen3-local", "claude-opus", "openai-mini"]
    assert "exec" in m.agent.tools
    assert m.agent.prompt_file  # relative path into the app dir

    # item display nouns
    assert m.item.noun == "Investigation"
    assert m.item.create_label == "Start Investigation"

    # layout + labels
    assert m.layout.list_ == ["severity", "status", "product"]  # JSON key "list"
    assert m.labels["severity"] == "Severity"
    assert m.default_profile == "default"
