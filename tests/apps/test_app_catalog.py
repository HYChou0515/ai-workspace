import pytest

from workspace_app.apps.catalog import (
    AppCatalog,
    _subset_or_raise,
    discover_app_slugs,
    validate_all_apps,
    validate_function_coherence,
)
from workspace_app.apps.manifest import (
    AgentManifest,
    AppManifest,
    FunctionToggles,
    ItemNouns,
    Layout,
)
from workspace_app.config.schema import Preset


def _presets() -> dict[str, Preset]:
    return {
        "qwen3-local": Preset(model="ollama_chat/qwen3:14b", description="local"),
        "claude-opus": Preset(model="claude-opus-4-7", description="hosted"),
        "openai-mini": Preset(model="openai/gpt-4o-mini"),
    }


# ─── 3-layer resolve ────────────────────────────────────────────────
def test_resolve_default_inherits_ceiling_and_uses_chosen_preset():
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="claude-opus"
    )
    assert cfg.model == "claude-opus-4-7"  # preset supplies model/creds
    assert cfg.name == "RCA · Claude Opus"  # picker display name
    assert {"exec", "rca-tools"} <= set(cfg.allowed_tools or [])  # full ceiling
    assert "RCA Agent" in cfg.system_prompt  # app base prompt
    assert "SOP.md" in cfg.system_prompt  # + default profile appendix
    # default profile has no suggestions → App fallback
    assert [s.prompt for s in cfg.suggestions] == [
        "Show the SPC analysis",
        "Run a Pareto of defect modes",
        "Draft the report",
    ]


def test_resolved_rca_prompt_carries_fe_renderer_conventions():
    """#94 moved the artifact conventions from a shared base prompt into each
    App's prompt. The resolved RCA agent prompt must still teach versioned
    reports (`/report.v`) and notebooks (`.ipynb`) — the FE renderers depend on
    these. The one-tool-call-per-turn rule (the LiteLLM small-model bug
    workaround) is a cross-App invariant, so it now lives in the shared `_base`
    preamble — but it must still reach RCA's resolved prompt."""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="qwen3-local"
    )
    for marker in ("/report.v", ".ipynb"):
        assert marker in cfg.system_prompt
    assert "one tool call" in cfg.system_prompt.lower()


def test_resolve_profile_narrows_tools_and_presets():
    cfg = AppCatalog(presets=_presets()).resolve(app_slug="rca", profile="tool-demo")
    assert cfg.model == "ollama_chat/qwen3:14b"  # only qwen3-local allowed → first
    assert "data-fetch" in (cfg.allowed_tools or [])
    assert "rca-tools" not in (cfg.allowed_tools or [])  # narrowed subset
    assert cfg.suggestions  # tool-demo's own chips, not the App fallback


def test_resolve_ignores_attached_preset_outside_the_allowed_subset():
    # tool-demo allows only qwen3-local; an attached claude-opus is ignored.
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="tool-demo", attached_preset="claude-opus"
    )
    assert cfg.model == "ollama_chat/qwen3:14b"


def test_resolve_applies_tool_prefs_force_off():
    """#322: a per-item tool pref of ``{tool: False}`` removes that tool from the
    resolved set; untouched tools follow the (here: full ceiling) default."""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca",
        profile="default",
        attached_preset="qwen3-local",
        tool_prefs={"rca-tools": False},
    )
    assert "rca-tools" not in (cfg.allowed_tools or [])  # forced off
    assert "exec" in (cfg.allowed_tools or [])  # untouched → follows default (on)


def test_tool_prefs_force_on_overrides_profile_narrowing_within_ceiling():
    """#322: the override ceiling is the App's tools, NOT the profile. tool-demo
    narrows away ``rca-tools``; a per-item force-ON re-adds it because it is in
    the App ceiling."""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="tool-demo", tool_prefs={"rca-tools": True}
    )
    assert "rca-tools" in (cfg.allowed_tools or [])


def test_tool_prefs_ignores_keys_outside_the_app_ceiling():
    """#322: a stale/bogus pref key (not an App tool) is a no-op — it never
    appears in the resolved set."""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca",
        profile="default",
        attached_preset="qwen3-local",
        tool_prefs={"totally-made-up": True},
    )
    assert "totally-made-up" not in (cfg.allowed_tools or [])


def test_tool_prefs_all_off_yields_empty_toolset():
    """#322: forcing every default tool OFF resolves to an explicit empty set —
    a valid 'plain chat agent' state."""
    rca_default = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="qwen3-local"
    )
    prefs = {t: False for t in (rca_default.allowed_tools or [])}
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="qwen3-local", tool_prefs=prefs
    )
    assert cfg.allowed_tools == []


def test_resolve_raises_when_chosen_preset_not_declared():
    cat = AppCatalog(presets={"openai-mini": Preset(model="x")})
    with pytest.raises(ValueError, match="not declared"):
        cat.resolve(app_slug="rca", profile="default", attached_preset="claude-opus")


# ─── subset validation helper ───────────────────────────────────────
def test_subset_or_raise():
    _subset_or_raise(["a"], ["a", "b"], kind="tools", app="x", profile="p")  # ok
    with pytest.raises(ValueError, match="tools"):
        _subset_or_raise(["a", "z"], ["a", "b"], kind="tools", app="x", profile="p")


def test_compose_prompt_joins_present_sections_and_skips_empty():
    from workspace_app.apps.catalog import _compose_prompt
    from workspace_app.apps.skills import SkillMeta

    skill = SkillMeta(name="report-format", description="how to lay out the report")
    # base only — no appendix, no skills (the empty-appendix path)
    assert _compose_prompt("BASE", "", []) == "BASE"
    # appendix appends; the skill index is advertised under its own heading
    full = _compose_prompt("BASE", "APPENDIX", [skill])
    assert full.startswith("BASE\n\nAPPENDIX")
    assert "## Available skills" in full
    assert "- `report-format`: how to lay out the report" in full


# ─── #241 shared workspace preamble ─────────────────────────────────
def test_compose_prompt_inserts_preamble_after_base():
    """#241: the shared workspace preamble sits between the App's identity
    (base) and the profile appendix. An empty preamble (a non-workspace App)
    is omitted entirely."""
    from workspace_app.apps.catalog import _compose_prompt

    assert _compose_prompt("BASE", "APPENDIX", [], preamble="PRE") == "BASE\n\nPRE\n\nAPPENDIX"
    assert _compose_prompt("BASE", "", [], preamble="") == "BASE"


def test_resolved_workspace_prompt_carries_base_preamble():
    """#241: a workspace App's resolved prompt teaches workspace awareness +
    guardrails — prefer function tools over the shell (`list_files` / `read_image`,
    not `exec`-ed shell), orient before answering, and stay in scope."""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="qwen3-local"
    )
    assert "## Working in this workspace" in cfg.system_prompt
    assert "list_files" in cfg.system_prompt
    assert "read_image" in cfg.system_prompt
    assert "scope" in cfg.system_prompt.lower()


def test_rca_prompt_no_longer_endorses_exec_cat():
    """#241: with the hard tool-over-shell rule in the preamble, the RCA prompt
    must not still advertise `exec` as the way to `cat` a file — reading goes
    through `read_file`. (The preamble's negative `exec(["cat"…])` example may
    mention cat; this targets the old positive endorsement string.)"""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="qwen3-local"
    )
    assert "`git`, `cat`" not in cfg.system_prompt


def test_kb_chat_prompt_has_no_workspace_preamble():
    """#241: the KB chat agent has no workspace/sandbox — it must NOT inherit the
    workspace preamble (it goes through the preset pipeline, not AppCatalog)."""
    from workspace_app.kb.prompts import load_kb_system_prompt

    assert "## Working in this workspace" not in load_kb_system_prompt()


# ─── shared sandbox preamble (cross-app consolidation) ──────────────
def test_compose_prompt_inserts_sandbox_preamble_after_base_preamble():
    """The sandbox preamble sits between the shared workspace preamble and the
    profile appendix: base → preamble → sandbox → appendix. An empty sandbox
    preamble (a non-sandbox App) is omitted entirely."""
    from workspace_app.apps.catalog import _compose_prompt

    assert (
        _compose_prompt("BASE", "APPENDIX", [], preamble="PRE", sandbox_preamble="SBX")
        == "BASE\n\nPRE\n\nSBX\n\nAPPENDIX"
    )
    # empty sandbox preamble drops out, leaving the #241 ordering intact
    assert (
        _compose_prompt("BASE", "APPENDIX", [], preamble="PRE", sandbox_preamble="")
        == "BASE\n\nPRE\n\nAPPENDIX"
    )


def test_sandbox_app_resolved_prompt_carries_sandbox_preamble():
    """A `function.sandbox` App's resolved prompt teaches running commands with
    `exec` — the cross-app sandbox guidance now lives in the shared `_sandbox`
    preamble, not in each App's own prompt."""
    cfg = AppCatalog(presets=_presets()).resolve(
        app_slug="rca", profile="default", attached_preset="qwen3-local"
    )
    assert "## Running commands" in cfg.system_prompt
    assert "exec" in cfg.system_prompt


def test_sandboxless_workspace_app_omits_sandbox_preamble():
    """A workspace App with `function.sandbox: false` (the `_template` App) still
    gets the shared workspace preamble but NOT the sandbox preamble — it has no
    `exec`, so shell/`exec`/python-via-exec guidance would only mislead it."""
    cfg = AppCatalog(presets=_presets()).resolve(app_slug="_template", profile="default")
    assert "## Working in this workspace" in cfg.system_prompt  # _base still applies
    assert "## Running commands" not in cfg.system_prompt  # _sandbox gated out


def test_security_guardrail_does_not_enumerate_root_as_offlimits():
    """Regression: the shared workspace preamble must NOT hard-code `/root` as an
    off-limits path. In the default chroot sandbox backend the workspace IS `/root`
    (the agent's cwd + $HOME), so listing it as forbidden contradicts reality. The
    guardrail is expressed backend-agnostically: workspace = your cwd / `~`."""
    for slug in ("rca", "playground", "topic-hub", "_template"):
        cfg = AppCatalog(presets=_presets()).resolve(
            app_slug=slug, profile="default", attached_preset="qwen3-local"
        )
        assert "/root" not in cfg.system_prompt, f"{slug} prompt still lists /root"


def test_every_workspace_app_inherits_base_preamble():
    """All workspace Apps (the discoverable three) inherit the shared `_base`
    workspace preamble — cross-app workspace awareness lives there, not per App."""
    for slug in discover_app_slugs():
        cfg = AppCatalog(presets=_presets()).resolve(
            app_slug=slug, profile="default", attached_preset="qwen3-local"
        )
        assert "## Working in this workspace" in cfg.system_prompt, slug


# ─── function ↔ tools coherence (startup hard error) ────────────────
def _manifest(
    *, tools, workspace=True, sandbox=True, terminal=True, primary_surface="chat"
) -> AppManifest:
    return AppManifest(
        slug="x",
        title="X",
        agent=AgentManifest(prompt_file="prompts/system.md", tools=tools),
        item=ItemNouns(noun="Item", noun_plural="Items"),
        function=FunctionToggles(workspace=workspace, sandbox=sandbox, terminal=terminal),
        layout=Layout(primary_surface=primary_surface),
    )


def test_coherence_ok_for_rca_shaped_app():
    validate_function_coherence(_manifest(tools=["exec", "read_file"]))  # no raise


def test_coherence_terminal_requires_sandbox():
    with pytest.raises(ValueError, match="terminal"):
        validate_function_coherence(_manifest(tools=[], sandbox=False, terminal=True))


def test_coherence_exec_requires_sandbox():
    with pytest.raises(ValueError, match="sandbox"):
        validate_function_coherence(_manifest(tools=["exec"], sandbox=False, terminal=False))


def test_coherence_file_tools_require_workspace():
    with pytest.raises(ValueError, match="workspace"):
        validate_function_coherence(_manifest(tools=["read_file"], workspace=False))


def test_coherence_ide_primary_surface_requires_workspace():
    """#159: an `ide`-first layout has no IDE to show when `function.workspace`
    is false — an incoherent combination, caught at startup."""
    with pytest.raises(ValueError, match="primary_surface"):
        validate_function_coherence(
            _manifest(
                tools=[], workspace=False, sandbox=False, terminal=False, primary_surface="ide"
            )
        )


def test_coherence_chat_primary_surface_ok_without_workspace():
    """A chat-first App with no IDE is the canonical pure-chat shape — coherent."""
    validate_function_coherence(
        _manifest(tools=[], workspace=False, sandbox=False, terminal=False, primary_surface="chat")
    )  # no raise


# ─── discovery + startup validation ─────────────────────────────────
def test_discover_app_slugs_includes_rca():
    assert "rca" in discover_app_slugs()


def test_validate_all_apps_passes_for_bundled_apps():
    validate_all_apps()  # no raise — every bundled App (rca) is coherent
