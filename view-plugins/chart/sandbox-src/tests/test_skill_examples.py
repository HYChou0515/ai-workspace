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


def test_the_skill_table_names_every_channel_the_schema_requires():
    # The "needs" column restates markChannels for the model; hold it to it.
    from chart_view.spec import spec_schema

    rows = {
        m: cells[-1]
        for line in SKILL.splitlines()
        if line.startswith("| `") and (cells := [c.strip() for c in line.strip("|").split("|")])
        for m in re.findall(r"`(\w+)`", cells[0])
    }
    for rule in spec_schema()["$defs"]["markChannels"]["allOf"]:
        marks = [a["const"] for a in rule["if"]["properties"]["mark"]["anyOf"] if "const" in a]
        encoding = rule["then"]["properties"]["encoding"]
        needed = encoding.get("required", []) or [r["required"][0] for r in encoding["anyOf"]]
        for mark in marks:
            for channel in needed:
                assert f"`{channel}`" in rows[mark], (mark, channel)


def test_every_mark_the_schema_has_is_in_the_skill_table():
    from chart_view.spec import spec_schema

    first_cells = re.findall(r"^\| ([^|]*) \|", SKILL, flags=re.M)
    in_table = {m for cell in first_cells for m in re.findall(r"`(\w+)`", cell)}
    assert set(spec_schema()["$defs"]["markType"]["enum"]) <= in_table


# Q23 (docs/plan-view-plugins.md): a wafer map is a generic map, and the domain
# words in #847/#848 are examples. The skill is what every model copies, so
# its examples use generic column names, and so do the scenarios that score it.
DOMAIN = re.compile(r"\b(lots?|wafers?|dies?|die_[xy]|yield|defects?|fab)\b", re.I)


def test_the_skill_teaches_no_one_domain():
    assert DOMAIN.findall(SKILL) == []


def test_the_skill_scenarios_are_not_one_domain_either():
    scenarios = Path(__file__).resolve().parents[2] / "scenarios"
    found = {p.name: DOMAIN.findall(p.read_text()) for p in sorted(scenarios.iterdir())}
    assert found and {name: words for name, words in found.items() if words} == {}
