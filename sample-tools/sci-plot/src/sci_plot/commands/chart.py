"""``chart`` command: render one of the registered scientific charts to a PNG.

``Args`` is auto-assembled from the chart registry (a discriminated union on
``chart``), so this module stays tiny as the catalog grows — a new chart is a
new ``IChart`` subclass, not an edit here.
"""

from __future__ import annotations

import json
from datetime import datetime

import sci_plot.charts  # noqa: F401  (import-for-registration: populate the registry)
from sci_plot.framework.registry import build_request_model, run_chart

Args = build_request_model()

DESCRIPTION = (
    "Render a scientific chart from tabular data and write a PNG. Pick a chart "
    "type via `chart` and pass `data` (a workspace file path or inline JSON) "
    "plus the chart's columns. Output is a JSON object with an `images` key "
    "listing the written path(s). If a column role can't be matched "
    "automatically the result is a `needs_input` object listing which columns "
    "to specify — call again with them."
)


def run(args: Args) -> None:  # type: ignore[valid-type]
    result = run_chart(args, datetime.now())
    print(json.dumps(result, indent=2))
