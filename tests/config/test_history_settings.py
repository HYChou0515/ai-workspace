"""#624 follow-up: the one setting on this ladder that is a MULTIPLIER.

Every other number in `history` is a token count, where a typo is a number
visibly too big or too small. This one is a fraction, and the typo that matters
is a missing decimal point — `8` looks entirely reasonable in a field that takes
numbers. It just claims the input window is eight times what the endpoint said,
which surfaces as a rejected request on every turn, on exactly the deployment
that set the key because compaction was not working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.loader import load


def _cfg(tmp_path: Path, ratio: object) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(f"history:\n  max_tokens_window_ratio: {ratio}\n")
    return path


@pytest.mark.parametrize("ratio", [0.5, 0.8, 1.0, 0.2])
def test_a_fraction_is_accepted(tmp_path: Path, ratio: float):
    """The documented range, both ends included: 1.0 says "take max_tokens at
    face value", which is right for an endpoint whose two figures are equal
    (measured: `ollama_chat/qwen3:14b` reports 40,960 for both)."""
    settings = load(config_path=_cfg(tmp_path, ratio), env={})
    assert settings.history.max_tokens_window_ratio == ratio


@pytest.mark.parametrize("ratio", [8, 1.5, -0.5, 0])
def test_a_value_outside_the_range_is_refused_at_load(tmp_path: Path, ratio: object):
    """`8` is the missing decimal point. `1.5` claims a window larger than the
    endpoint's own figure. `0` and negatives derive no window at all, which
    reads downstream as "no ceiling known" and turns off the very fallback the
    operator was configuring — the dead-knob failure, self-inflicted."""
    with pytest.raises(ValueError, match="max_tokens_window_ratio"):
        load(config_path=_cfg(tmp_path, ratio), env={})


def test_the_message_names_the_likely_typo(tmp_path: Path):
    """An operator who wrote `8` needs to be told `0.8`, not just "invalid"."""
    with pytest.raises(ValueError, match="0.8"):
        load(config_path=_cfg(tmp_path, 8), env={})
