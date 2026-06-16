"""with_collection_guidance (#90) — append a collection's wiki guidance onto a
bundled wiki AgentConfig's system prompt.

The per-collection guidance is ADDITIVE: the bundled prompt (tools, citation
rules, step budget) stays the base, and the collection's text is appended as a
"## Collection-specific guidance" block — so an operator can shape a wiki's
domain/organisation without breaking the machinery, and an empty guidance is a
no-op.
"""

from __future__ import annotations

from workspace_app.kb.wiki.guidance import with_collection_guidance
from workspace_app.resources import AgentConfig, Collection, make_spec


def test_non_empty_guidance_is_appended_as_a_block():
    base = AgentConfig(name="Wiki Maintainer", system_prompt="BASE RULES")
    out = with_collection_guidance(base, "Organize pages by reflow zone.")
    assert out.system_prompt == (
        "BASE RULES\n\n## Collection-specific guidance\nOrganize pages by reflow zone."
    )
    # the bundled base is preserved, not replaced
    assert out.system_prompt.startswith("BASE RULES")


def test_blank_guidance_is_a_no_op():
    base = AgentConfig(name="Wiki Maintainer", system_prompt="BASE RULES")
    # empty and whitespace-only guidance must not append a hollow block
    assert with_collection_guidance(base, "").system_prompt == "BASE RULES"
    assert with_collection_guidance(base, "   \n  ").system_prompt == "BASE RULES"


def test_collection_carries_per_wiki_guidance_and_defaults_empty():
    """The two guidance fields round-trip through specstar persistence, and a
    collection created without them (every existing row) reads back blank — so
    no migration is needed."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(Collection)
    # a collection created the old way (no guidance) reads back with empty defaults
    plain = rm.get(rm.create(Collection(name="plain")).resource_id).data
    assert isinstance(plain, Collection)
    assert plain.wiki_maintainer_guidance == "" and plain.wiki_reader_guidance == ""
    # set values persist and come back intact
    cid = rm.create(
        Collection(
            name="c",
            use_wiki=True,
            wiki_maintainer_guidance="Organize by zone.",
            wiki_reader_guidance="Answer tersely.",
        )
    ).resource_id
    got = rm.get(cid).data
    assert isinstance(got, Collection)
    assert got.wiki_maintainer_guidance == "Organize by zone."
    assert got.wiki_reader_guidance == "Answer tersely."
