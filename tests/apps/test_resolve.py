from specstar import SpecStar

from workspace_app.apps.catalog import AppCatalog
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import find_work_item, resolve_item_agent_config
from workspace_app.config.schema import Settings
from workspace_app.resources import make_spec


def _app_catalog() -> AppCatalog:
    return AppCatalog(presets=Settings().agents.presets)


def test_resolve_new_app_item_uses_the_app_catalog():
    """A new per-App WorkItem resolves its turn's AgentConfig via the 3-layer
    AppCatalog — model from its chosen preset, system prompt from the App."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(RcaInvestigation)
    rid = rm.create(
        RcaInvestigation(title="t", owner="u", attached_preset="claude-opus")
    ).resource_id

    cfg = resolve_item_agent_config(spec, _app_catalog(), rid)
    assert cfg is not None
    assert cfg.model == "claude-opus-4-7"  # the chosen preset
    assert "RCA Agent" in cfg.system_prompt  # the App's base prompt
    assert "rca-tools" in (cfg.allowed_tools or [])  # the App ceiling (default profile)


def test_resolve_item_applies_its_tool_prefs():
    """#322: a per-item ``attached_tool_prefs`` tri-state override flows from the
    stored WorkItem into the resolved AgentConfig — here forcing ``rca-tools`` OFF
    while leaving the rest of the App ceiling intact."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(RcaInvestigation)
    rid = rm.create(
        RcaInvestigation(
            title="t",
            owner="u",
            attached_preset="claude-opus",
            attached_tool_prefs={"rca-tools": False},
        )
    ).resource_id

    cfg = resolve_item_agent_config(spec, _app_catalog(), rid)
    assert cfg is not None
    assert "rca-tools" not in (cfg.allowed_tools or [])  # forced off per-item
    assert "exec" in (cfg.allowed_tools or [])  # untouched → still on


def test_resolve_composes_the_profile_skill_index_into_the_prompt():
    """#89 T1d: a profile that ships skills (local-lab → report-format) has its
    (name, description) index composed into the resolved system prompt, so the
    agent knows what `read_skill` can load without calling it first."""
    cfg = _app_catalog().resolve(app_slug="rca", profile="local-lab", attached_preset=None)
    assert "## Available skills" in cfg.system_prompt
    assert "report-format" in cfg.system_prompt


def test_resolve_default_profile_advertises_the_apps_shared_skill():
    """#298: the App opts into the shared author-skill (agent.skills), so even the
    default profile — which ships no package skills of its own — advertises it. The
    co-authoring entry point is reachable in every profile."""
    cfg = _app_catalog().resolve(app_slug="rca", profile="default", attached_preset=None)
    assert "## Available skills" in cfg.system_prompt
    assert "author-skill" in cfg.system_prompt


def test_resolve_item_applies_its_skill_prefs():
    """#380: a per-item ``attached_skill_prefs`` tri-state override flows from the
    stored WorkItem into the resolved AgentConfig — here forcing ``author-skill``
    OFF so it drops out of the resolved prompt's skill index, the skill sibling of
    ``attached_tool_prefs``."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(RcaInvestigation)
    rid = rm.create(
        RcaInvestigation(
            title="t",
            owner="u",
            attached_preset="claude-opus",
            attached_skill_prefs={"author-skill": False},
        )
    ).resource_id

    cfg = resolve_item_agent_config(spec, _app_catalog(), rid)
    assert cfg is not None
    assert "author-skill" not in cfg.system_prompt  # forced off per-item


def test_resolve_profile_skills_narrows_the_default_on_shared_subset():
    """#380: a profile's ``skills`` list narrows the App's declared shared-skill
    ceiling to a default-ON subset (mirroring ``tools``). The ``_template``
    scaffold declares two shared skills but its default profile opts only
    ``author-skill`` in — so ``author-workflow`` is available-but-default-OFF and
    absent from the index, while ``author-skill`` stays advertised."""
    cfg = _app_catalog().resolve(app_slug="_template", profile="default", attached_preset=None)
    assert "author-skill" in cfg.system_prompt  # in profile.skills → default-on
    assert "author-workflow" not in cfg.system_prompt  # declared but not opted-in → default-off


def test_resolve_skill_prefs_force_on_readds_a_default_off_skill():
    """#380: a per-item ``skill_prefs`` True re-adds a skill the profile left
    default-OFF (its ceiling is the App's declared ``skills``, not the profile) —
    the escape hatch to pull in an available-but-off skill for this item."""
    cfg = _app_catalog().resolve(
        app_slug="_template",
        profile="default",
        attached_preset=None,
        skill_prefs={"author-workflow": True},
    )
    assert "author-workflow" in cfg.system_prompt  # force-on re-adds the default-off skill


def test_resolve_skill_prefs_force_off_drops_a_skill_from_the_index():
    """#380: a per-item ``attached_skill_prefs`` tri-state override forces a
    declared skill OFF, so its (name, description) line no longer appears in the
    resolved system prompt's "## Available skills" index — the disabled skill is
    hidden from the agent, mirroring the tool picker's force-off."""
    cfg = _app_catalog().resolve(
        app_slug="rca",
        profile="default",
        attached_preset=None,
        skill_prefs={"author-skill": False},
    )
    assert "author-skill" not in cfg.system_prompt


def test_resolve_unknown_item_returns_none():
    """An id that isn't any App's WorkItem (fresh/unknown) resolves to None —
    the turn falls back to the runner's own default."""
    spec = make_spec(default_user="u")
    cfg = resolve_item_agent_config(spec, AppCatalog(presets={}), "absent-id")
    assert cfg is None


def test_find_work_item_goes_straight_to_the_table_the_id_names():
    """A specstar id is `{resource_name}:{uuid}` — it names its own table. The
    scan asked every registered App in turn, and a miss is not free: it costs a
    `get` AND a `get_meta`. With `playground` ahead of `pm`, every request
    touching a pm item paid for a playground lookup first, and this runs in front
    of every hand-written route, several times per user action.

    That is only worth removing because each of those round-trips is expensive
    in production (~219ms measured), so the count IS the latency.
    """
    from workspace_app.apps.pm.model import PmProject
    from workspace_app.apps.registry import registered_apps
    from workspace_app.resources import make_spec

    spec = make_spec(default_user="u")
    rm_cls = type(spec.get_resource_manager(next(iter(registered_apps().values()))))
    pm_rm = spec.get_resource_manager(PmProject)
    item_id = pm_rm.create(PmProject(title="t", owner="u")).resource_id

    looked_up: list[str] = []
    original = rm_cls.get

    def counting_get(self, *args, **kwargs):
        looked_up.append(self.resource_name)
        return original(self, *args, **kwargs)

    rm_cls.get = counting_get  # ty: ignore[invalid-assignment]
    try:
        found = find_work_item(spec, item_id)
    finally:
        rm_cls.get = original

    assert found is not None
    assert found[0] == "pm"
    assert looked_up == ["pm-project"], looked_up  # no other App was asked


def test_an_unreadable_creator_does_not_fail_the_write_that_asked():
    """R12-7. `debtor_of` reads the store only when `owner` says nothing, and it
    swallows a failure there rather than propagating it.

    That is deliberate — the callers are a quota gate and a notification, and
    neither should turn a store hiccup into a 500 on somebody's write. But the
    guard had no test, and it is the one branch that produces the outcome the
    module says must not exist: a bill charged to nobody. Worth knowing it can
    happen, and worth knowing it is the ONLY way it can.

    `spec` is a parameter, so the seam needs no patching of module globals.
    """
    from workspace_app.apps.resolve import debtor_of

    class _Boom(SpecStar):
        """A real `SpecStar` whose store is down — subclassed rather than
        duck-typed because `ty` rejects the stand-in otherwise, and a double
        that implements less than the real thing is immune to exactly the
        changes worth catching."""

        def get_resource_manager(self, *_args: object, **_kwargs: object):  # noqa: ANN202
            raise RuntimeError("store is down")

    item = RcaInvestigation(title="t", owner="   ")

    assert debtor_of(_Boom(), "rca", "rca-investigation:x", item) == ""
