import msgspec

from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.schema import project_fields


def _by_name(fields, name):
    return next(f for f in fields if f.name == name)


def test_enum_field_projects_as_select_with_its_enum_values():
    """An enum domain field (`severity`: P0..P4) projects to a `select` whose
    options are the enum values in definition order — the FE's sole source of
    dropdown choices, derived from the model and never restated."""
    sev = _by_name(project_fields(RcaInvestigation), "severity")
    assert sev.kind == "select"
    assert sev.options == ["P0", "P1", "P2", "P3", "P4"]


def test_plain_string_field_projects_as_text_without_options():
    """A non-enum string field (`product`) projects to `text` and carries no
    options — the FE renders it as a click-to-edit input, not a dropdown."""
    prod = _by_name(project_fields(RcaInvestigation), "product")
    assert prod.kind == "text"
    assert prod.options is msgspec.UNSET


def test_list_field_projects_as_tags_without_options():
    """A `list[str]` domain field (`topics`) projects to `tags` so the FE renders
    a chip input (add/remove topics), not a single-line text box."""
    topics = _by_name(project_fields(RcaInvestigation), "topics")
    assert topics.kind == "tags"
    assert topics.options is msgspec.UNSET
