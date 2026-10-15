"""Write the dashboard's test fixtures from a world whose truth is known
(docs/plan-capacity-usage.md P13): the real summary, one person, one what-if —
the shapes the API really returns, so the page's tests cannot drift from them.

    uv run python scripts/capacity_fixtures.py

`tests/capacity/test_fe_fixtures.py` fails when the fixtures no longer validate
against the API's response models: rerun this then.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.capacity.world import World, WorldGpus, WorldLlm, WorldPods, WorldRecords  # noqa: E402
from workspace_app.capacity.analysis.model import analyse  # noqa: E402
from workspace_app.capacity.analysis.observe import observe  # noqa: E402
from workspace_app.capacity.analysis.period import Period  # noqa: E402
from workspace_app.capacity.sources import CapacitySources  # noqa: E402

OUT = ROOT / "web" / "src" / "capacity" / "fixtures"


def fixtures() -> dict[str, object]:
    w = World(days=28, users=16, seed=11)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))
    analysis = analyse(obs, now_ms=w.end_ms)
    summary = analysis.summary()
    for pair in summary["thresholds"]:  # keep the fixture small: 200 points a pair
        pair["points"] = pair["points"][:: max(1, len(pair["points"]) // 200)]
    n_now = summary["people"]["n_now"]
    return {
        "summary.json": summary,
        "person.json": analysis.person("user00"),
        "whatif.json": analysis.whatif(n_now * 2),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in fixtures().items():
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
