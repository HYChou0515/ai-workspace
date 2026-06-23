import msgspec

from workspace_app.apps.manifest import AppManifest, load_app_manifest

_MINIMAL = b"""{
  "slug": "x", "title": "X",
  "agent": {"prompt_file": "p.md"},
  "item": {"noun": "Item", "noun_plural": "Items"}
}"""


def test_manifest_decodes_onboarding_block():
    """An app.json may carry an `onboarding` block — versioned, read-only welcome
    content (title / intro / points) — that decodes into a typed `Onboarding`."""
    raw = b"""{
      "slug": "x", "title": "X",
      "agent": {"prompt_file": "p.md"},
      "item": {"noun": "Item", "noun_plural": "Items"},
      "onboarding": {
        "version": "1",
        "title": "Welcome to X",
        "intro": "What X does.",
        "points": [{"title": "Step one", "body": "Do this."}]
      }
    }"""
    m = msgspec.json.decode(raw, type=AppManifest)
    assert m.onboarding is not None
    assert m.onboarding.version == "1"
    assert m.onboarding.title == "Welcome to X"
    assert m.onboarding.intro == "What X does."
    assert [p.title for p in m.onboarding.points] == ["Step one"]
    assert m.onboarding.points[0].body == "Do this."


def test_manifest_onboarding_absent_is_none():
    """No `onboarding` block ⇒ the field is None (the default for most Apps)."""
    m = msgspec.json.decode(_MINIMAL, type=AppManifest)
    assert m.onboarding is None


def test_shipped_apps_carry_onboarding_teaching():
    """Every shipped App authors a versioned welcome teaching with a title and a
    couple of concrete steps, so new users get oriented when they enter it."""
    for slug in ("rca", "topic-hub", "playground"):
        ob = load_app_manifest(slug).onboarding
        assert ob is not None, f"{slug} is missing onboarding teaching"
        assert ob.version
        assert ob.title
        assert ob.intro
        assert len(ob.points) >= 2
        assert all(p.title and p.body for p in ob.points)


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
