from workspace_app.apps.manifest import Layout, load_app_manifest


def test_layout_primary_surface_defaults_to_chat():
    """#159: an App declares which surface leads when an item opens. The default
    is chat-first — the file IDE is tucked behind a `Workspace` toggle so the
    IDE metaphor reaches only the Apps that opt into it."""
    assert Layout().primary_surface == "chat"


def test_rca_declares_ide_primary_surface():
    """#159: RCA is an evidence/brief/notebook-heavy flow, so it opens the VS
    Code workspace up front (the one bundled App that opts into `ide`)."""
    assert load_app_manifest("rca").layout.primary_surface == "ide"


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
