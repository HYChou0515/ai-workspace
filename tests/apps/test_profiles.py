from msgspec import UNSET

from workspace_app.apps.profiles import list_profiles, load_profile, load_profile_appendix


def test_rca_lists_its_profiles():
    profs = list_profiles("rca")
    assert "default" in profs
    assert "tool-demo" in profs


def test_rca_ports_local_lab_and_smt_dropping_methodology():
    """#89 P8 closeout T1: the remaining RCA profiles are ported to the App,
    except `methodology` (the 5-Why / fishbone.canvas profile — dropped)."""
    profs = list_profiles("rca")
    assert "local-lab" in profs
    assert "smt-reflow-example" in profs
    assert "methodology" not in profs


def test_local_lab_profile_narrows_tools_and_has_a_clean_appendix():
    p = load_profile("rca", "local-lab")
    assert p.presets == ["qwen3-local"]
    assert "rca-tools" in p.tools  # local-lab needs the provisioned RCA tools  # ty: ignore
    ap = load_profile_appendix("rca", "local-lab")
    assert ap  # ships a prompt appendix
    low = ap.lower()
    assert "canvas" not in low and "5-why" not in low and "5 why" not in low


def test_smt_profile_appendix_drops_canvas_and_5why():
    ap = load_profile_appendix("rca", "smt-reflow-example")
    assert ap
    low = ap.lower()
    assert "canvas" not in low and "5-why" not in low and "5 why" not in low


def test_default_profile_inherits_the_app_ceiling_and_has_an_appendix():
    """`default` ships no `_profile.json`, so it overrides nothing (tools/presets
    UNSET = inherit the App's full ceiling) and just contributes its prompt
    appendix."""
    p = load_profile("rca", "default")
    assert p.tools is UNSET
    assert p.presets is UNSET
    assert p.suggestions == []

    appendix = load_profile_appendix("rca", "default")
    assert "SOP.md" in appendix
    assert "canvas" not in appendix.lower()  # dropped per house rule


def test_tool_demo_profile_narrows_tools_and_presets_to_a_subset():
    p = load_profile("rca", "tool-demo")
    assert p.presets == ["qwen3-local"]
    assert "data-fetch" in p.tools  # ty: ignore[unsupported-operator]
    assert "rca-tools" not in p.tools  # a strict subset of the App's ceiling  # ty: ignore
    assert p.suggestions  # quick-prompt chips
