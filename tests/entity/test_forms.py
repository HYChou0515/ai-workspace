"""Quick-create form derivation (#419 §D). The deterministic UI's form is the
skeleton's `{{arg}}` placeholders rendered as widgets — a field with no `{{arg}}`
(e.g. a hardcoded `status: open`) does NOT enter the form."""

from __future__ import annotations

from workspace_app.entity.catalog import EntityType
from workspace_app.entity.forms import form_spec
from workspace_app.entity.schema import EntitySchema, FieldSpec, Role


def _issue_type() -> EntityType:
    schema = EntitySchema(
        fields=[
            FieldSpec(name="title", role=Role.TEXT, required=True),
            FieldSpec(name="status", role=Role.STATUS, values=["open", "done"]),
        ]
    )
    skeleton = "---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"
    return EntityType(name="issue", schema=schema, skeleton=skeleton, records_path="issues")


def test_form_spec_derives_only_arg_fields_with_role_widgets() -> None:
    fields = form_spec(_issue_type())

    # `status` is hardcoded in the skeleton (no `{{arg}}`) → not in the form.
    assert [f.name for f in fields] == ["title", "body"]
    title, body = fields
    assert title.widget == "text"
    assert title.required is True  # `{{arg.title}}` has no `?`
    assert body.required is False  # `{{arg.body?}}` is optional


def test_ref_and_daterange_args_get_their_own_widgets() -> None:
    """A `ref` field is settable at create time (you pick the target record), so
    it enters the form with a `ref` widget; a `daterange` gets a `daterange`
    widget — both distinct from a plain text box."""
    schema = EntitySchema(
        fields=[
            FieldSpec(name="milestone", role=Role.REF, to="milestone"),
            FieldSpec(name="span", role=Role.DATERANGE),
        ]
    )
    entity_type = EntityType(
        name="issue",
        schema=schema,
        skeleton="milestone: {{arg.milestone?}}\nspan: {{arg.span?}}\n",
        records_path="issues",
    )
    widgets = {f.name: f.widget for f in form_spec(entity_type)}
    assert widgets == {"milestone": "ref", "span": "daterange"}


def test_compute_on_read_arg_degrades_to_text_instead_of_crashing() -> None:
    """A skeleton that (mistakenly) exposes a compute-on-read role as an `{{arg}}`
    must not crash form derivation — the fault-tolerance rule (§E) says degrade,
    so an unmapped role falls back to a plain text widget."""
    schema = EntitySchema(fields=[FieldSpec(name="progress", role=Role.ROLLUP, agg="avg")])
    entity_type = EntityType(
        name="m",
        schema=schema,
        skeleton="progress: {{arg.progress}}\n",
        records_path="m",
    )
    assert form_spec(entity_type)[0].widget == "text"


def test_duplicate_arg_placeholder_is_deduped() -> None:
    entity_type = EntityType(
        name="t",
        schema=EntitySchema(fields=[]),
        skeleton="{{arg.x}} again {{arg.x}}",
        records_path="t",
    )

    assert [f.name for f in form_spec(entity_type)] == ["x"]
