"""The chart skill's examples are specs the schema accepts.

A model copies the skill's YAML nearly verbatim, so an example the schema
refuses teaches a refusal. The skill is the plugin's own `skill/SKILL.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

from chart_view.spec import parse_spec, spec_errors

SKILL = (Path(__file__).resolve().parents[2] / "skill" / "SKILL.md").read_text()
BLOCKS = re.findall(r"```yaml\n(.*?)```", SKILL, flags=re.S)


def test_the_skill_has_its_examples():
    assert len(BLOCKS) == 3


def test_the_facet_example_is_valid_on_a_grid():
    """#848: the gallery's own example, dropped into a grid spec as a model
    would drop it."""
    spec = parse_spec(
        "view: chart\nsource: a.csv\nmark: grid\nencoding:\n"
        "  x: {field: die_x, type: ordinal}\n  y: {field: die_y, type: ordinal}\n"
        "  color: {field: bin, type: nominal}\n" + BLOCKS[2]
    )
    assert "facet" in spec
    assert spec_errors(spec) == []


def test_the_full_example_is_a_valid_spec():
    assert spec_errors(parse_spec(BLOCKS[0])) == []


def test_every_transform_example_is_valid():
    spec = parse_spec(
        "view: chart\nsource: a.csv\nmark: grid\nencoding:\n"
        "  x: {field: die_x, type: ordinal}\n  y: {field: die_y, type: ordinal}\n"
        "  color: {field: delta, type: quantitative}\n" + BLOCKS[1]
    )
    assert len(spec["transform"]) == 4
    assert spec_errors(spec) == []


def test_every_mark_the_schema_has_is_in_the_skill_table():
    from chart_view.spec import spec_schema

    first_cells = re.findall(r"^\| ([^|]*) \|", SKILL, flags=re.M)
    in_table = {m for cell in first_cells for m in re.findall(r"`(\w+)`", cell)}
    assert set(spec_schema()["$defs"]["markType"]["enum"]) <= in_table
