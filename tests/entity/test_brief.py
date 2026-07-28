"""The agent-facing schema brief (#pm) — the prompt section that tells the agent
what fields + status values a record type has, so it can create valid records
instead of guessing."""

from __future__ import annotations

from workspace_app.entity.brief import entity_schema_brief
from workspace_app.entity.catalog import EntityCatalog, EntityType
from workspace_app.entity.schema import EntitySchema, FieldSpec, Role


def _catalog(*types: EntityType) -> EntityCatalog:
    return EntityCatalog({t.name: t for t in types})


def _issue() -> EntityType:
    return EntityType(
        name="issue",
        records_path="issues",
        skeleton="",
        schema=EntitySchema(
            fields=[
                FieldSpec(name="title", role=Role.TEXT, required=True),
                FieldSpec(
                    name="status",
                    role=Role.STATUS,
                    values=["open", "in_progress", "blocked", "done"],
                ),
                FieldSpec(name="assignee", role=Role.ACTOR),
                FieldSpec(name="due", role=Role.DATE),
                FieldSpec(name="span", role=Role.DATERANGE),
                FieldSpec(name="progress", role=Role.PROGRESS),
                FieldSpec(name="milestone", role=Role.REF, to="milestone"),
                # manual board/table order — infra, auto-assigned / drag-set
                FieldSpec(name="rank", role=Role.RANK),
            ]
        ),
    )


def _milestone() -> EntityType:
    return EntityType(
        name="milestone",
        records_path="milestones",
        skeleton="",
        schema=EntitySchema(
            fields=[
                FieldSpec(name="title", role=Role.TEXT, required=True),
                FieldSpec(name="status", role=Role.STATUS, values=["planned", "active", "done"]),
                FieldSpec(name="span", role=Role.DATERANGE),
                # derived — must be omitted from create guidance
                FieldSpec(name="issues", role=Role.BACKREF, from_="issue.milestone"),
                FieldSpec(
                    name="progress", role=Role.ROLLUP, over="issues", agg="avg", field="progress"
                ),
            ]
        ),
    )


def test_lists_each_type_with_its_records_path() -> None:
    brief = entity_schema_brief(_catalog(_issue(), _milestone()))
    assert "**issue** (issues/N.md)" in brief
    assert "**milestone** (milestones/N.md)" in brief


def test_enumerates_the_closed_status_vocabulary() -> None:
    brief = entity_schema_brief(_catalog(_issue()))
    # the exact allowed values, so the model can't invent "todo" (which would lint)
    assert "status (one of: open, in_progress, blocked, done)" in brief


def test_spells_out_the_timeline_date_range_field() -> None:
    # the #4 gap: an issue with no span never appears on the gantt
    brief = entity_schema_brief(_catalog(_issue()))
    assert "span" in brief
    assert "timeline / gantt" in brief
    assert "YYYY-MM-DD/YYYY-MM-DD" in brief


def test_marks_required_and_names_actor_and_ref_conventions() -> None:
    brief = entity_schema_brief(_catalog(_issue()))
    assert "title (text, required)" in brief
    assert "assignee (a person — pass a user id from lookup_user)" in brief
    assert "milestone (a reference to a milestone — pass its number, or use link_entity)" in brief


def test_omits_derived_backref_and_rollup_fields() -> None:
    brief = entity_schema_brief(_catalog(_milestone()))
    # `issues` (backref) + `progress` (rollup) are read-only projections
    assert "issues" not in brief
    assert "progress" not in brief


def test_omits_the_rank_field_agent_never_sets_manual_order() -> None:
    # `rank` is manual board/table order — auto/drag-assigned, not agent-picked
    brief = entity_schema_brief(_catalog(_issue()))
    assert "rank" not in brief


def test_empty_catalog_injects_nothing() -> None:
    assert entity_schema_brief(_catalog()) == ""


def test_number_field_tells_the_agent_it_is_a_number() -> None:
    """Without this the agent is told "text" and writes `exp_days: "three"`."""
    catalog = EntityCatalog(
        {
            "issue": EntityType(
                name="issue",
                records_path="issues",
                schema=EntitySchema(fields=[FieldSpec(name="exp_days", role=Role.NUMBER)]),
                skeleton="---\nexp_days: {{arg.exp_days?}}\n---\n",
            )
        }
    )
    assert "number" in entity_schema_brief(catalog)
