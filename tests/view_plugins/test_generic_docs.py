"""The view-plugin platform is a general tool, not one built for one industry
(docs/plan-view-plugins.md Q23; the user, 2026-09-25). #847/#848 use wafers and
lots as their example, but what a person or a model copies — the guides, the
plugins' prompt lines, the worked example — names no domain."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# the chart skill's own guard (view-plugins/chart/sandbox-src/tests/
# test_skill_examples.py) plus the Chinese words the guides are written in
DOMAIN = re.compile(r"\b(lots?|wafers?|dies?|die_[xy]|yield|defects?|fab)\b|晶圓|良率|批號|機台", re.I)

COPIED = [
    "docs/view-plugin-chart.md",
    "docs/view-kind-authoring.md",
    "view-plugins/csv-table/web/src/CsvTableView.tsx",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "view-plugins").glob("*/plugin.json")),
]


def test_what_people_and_models_copy_names_no_one_domain():
    found = {rel: DOMAIN.findall((ROOT / rel).read_text()) for rel in COPIED}
    assert {rel: words for rel, words in found.items() if words} == {}
