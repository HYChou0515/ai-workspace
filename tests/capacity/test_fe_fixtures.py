"""The page's test fixtures are what the API really returns (docs/plan-capacity-usage.md
P13): a double whose shape drifted from the real one tests a page nobody runs.
Regenerate with `uv run python scripts/capacity_fixtures.py`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.api.capacity_routes import CapacitySummary, PersonOut, WhatIfOut

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "capacity" / "fixtures"


@pytest.mark.parametrize(
    ("name", "model"),
    [("summary.json", CapacitySummary), ("person.json", PersonOut), ("whatif.json", WhatIfOut)],
)
def test_each_fixture_is_a_valid_response(name, model):
    model.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _keys(node: object, path: str = "") -> set[str]:
    """Every key path in a JSON tree (list items share their first item's paths)."""
    if isinstance(node, dict):
        out = set()
        for k, v in node.items():
            here = f"{path}.{k}"
            out |= {here} | _keys(v, here)
        return out
    if isinstance(node, list) and node and isinstance(node[0], dict):
        return _keys(node[0], f"{path}[]")
    return set()


def test_the_summary_fixture_has_every_field_a_fresh_one_has():
    import scripts.capacity_fixtures as gen

    fresh = gen.fixtures()["summary.json"]
    kept = json.loads((FIXTURES / "summary.json").read_text(encoding="utf-8"))

    assert _keys(fresh) == _keys(kept), "rerun scripts/capacity_fixtures.py"


@pytest.mark.parametrize(
    ("name", "model"),
    [("summary.json", CapacitySummary), ("person.json", PersonOut), ("whatif.json", WhatIfOut)],
)
def test_the_api_passes_on_every_field_the_analysis_makes(name, model):
    """A response model silently drops a field it does not declare: the page's
    tests, run on the analysis' own output, would then pass on a field the
    page never receives."""
    import scripts.capacity_fixtures as gen

    fresh = json.loads(json.dumps(gen.fixtures()[name]))

    assert _keys(model.model_validate(fresh).model_dump()) == _keys(fresh)
