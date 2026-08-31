"""`turn_facts` resolves the item once; it must answer what the accessors answer.

It exists only to spare the turn path three specstar round trips, so the day it
disagrees with `slug_of` / `profile_of` / `skill_prefs_of` / `env_vars_of` it
stops being an optimisation and becomes a second, quietly different source of
truth — including for an unknown id, where the accessors' fallbacks differ from
each other (`None` slug, but `"default"` profile).
"""

from __future__ import annotations

from workspace_app.api.locator import ItemLocator
from workspace_app.apps.catalog import AppCatalog
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.config.schema import Settings
from workspace_app.resources import make_spec


def _locator_and_item():
    spec = make_spec(default_user="u")
    locator = ItemLocator(spec, AppCatalog(presets=Settings().agents.presets))
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(
            PlaygroundItem(
                title="t",
                owner="u",
                profile="echo",
                attached_skill_prefs={"grill-me": False},
                env_vars={"TZ": "Asia/Taipei"},
            )
        )
        .resource_id
    )
    return locator, item_id


def test_turn_facts_answers_exactly_what_the_accessors_answer():
    locator, item_id = _locator_and_item()

    facts = locator.turn_facts(item_id)

    assert facts.slug == locator.slug_of(item_id)
    assert facts.profile == locator.profile_of(item_id)
    assert facts.skill_prefs == locator.skill_prefs_of(item_id)
    assert facts.env_vars == locator.env_vars_of(item_id)


def test_turn_facts_matches_the_accessors_for_an_unknown_id():
    locator, _item_id = _locator_and_item()

    facts = locator.turn_facts("no-such-item")

    assert facts.slug == locator.slug_of("no-such-item")
    assert facts.profile == locator.profile_of("no-such-item")
    assert facts.skill_prefs == locator.skill_prefs_of("no-such-item")
    assert facts.env_vars == locator.env_vars_of("no-such-item")
