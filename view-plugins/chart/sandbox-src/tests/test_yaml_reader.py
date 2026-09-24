"""The YAML 1.2 core values the corpus cannot pin, because JSON has no way to
write them: infinities and NaN. js-yaml reads `.inf` / `-.Inf` / `.NaN` as
Infinity / -Infinity / NaN (probed on 5.2.0, the host's version)."""

from __future__ import annotations

import math

from chart_view.spec import parse_spec


def test_infinities_and_nan_read_as_floats():
    doc = parse_spec("a: [.inf, -.Inf, +.INF, .NaN, .nan]")
    a, b, c, d, e = doc["a"]
    assert a == math.inf and b == -math.inf and c == math.inf
    assert math.isnan(d) and math.isnan(e)
